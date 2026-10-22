"""Disabled real-model composition for a controlled public-chat evaluation.

No application startup code constructs this provider. When explicitly injected,
it still requires a tenant-owned immutable release decision for an exact bounded
activation manifest. The production configuration rejects the feature flag.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.conversation_message import ConversationMessage
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.public_chat import PublicChatSession
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.services.model_eval_controller import (
    EvaluationBlockedError,
    EvaluationPolicy,
    ExactInputTokenCountTransport,
    InputTokenCountPolicy,
    ModelEvaluationController,
)
from app.services.model_eval_openai_adapter import (
    DisconnectedResponsesEvaluationTransport,
    GroundedTurn,
    ProviderDisconnectedResponsesAdapter,
)
from app.services.offline_grounded_chat import offline_grounded_chat

ACTIVATION_PURPOSE = "controlled_public_chat_model_evaluation"


class SnapshotSessionGuard:
    """Check handoff without holding a database lock across provider I/O."""

    async def assert_generation_allowed(
        self, db: AsyncSession, *, organization_id: int, session_id: int
    ) -> None:
        status = await db.scalar(
            select(PublicChatSession.status).where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
            )
        )
        if status != "ai_active":
            raise EvaluationBlockedError(
                "persisted session state suppresses autonomous generation"
            )


@dataclass(frozen=True)
class ApprovedEvaluationActivation:
    run_id: str
    organization_id: int
    public_chat_session_id: int


class PersistedEvaluationActivationGuard:
    """Resolve one bounded activation from immutable tenant-owned records."""

    @staticmethod
    async def require(
        db: AsyncSession, *, organization_id: int, run_id: str,
        public_chat_session_id: int,
        runtime_count_policy: InputTokenCountPolicy,
    ) -> ApprovedEvaluationActivation:
        runtime_count_policy.validate(expected_model=EvaluationPolicy().model)
        row = (
            await db.execute(
                select(AIEvaluationRun, AIEvaluationReleaseDecision)
                .join(
                    AIEvaluationReleaseDecision,
                    (AIEvaluationReleaseDecision.candidate_run_id == AIEvaluationRun.id)
                    & (
                        AIEvaluationReleaseDecision.organization_id
                        == AIEvaluationRun.organization_id
                    ),
                )
                .where(
                    AIEvaluationRun.run_id == run_id,
                    AIEvaluationRun.organization_id == organization_id,
                )
                .order_by(
                    AIEvaluationReleaseDecision.created_at.desc(),
                    AIEvaluationReleaseDecision.id.desc(),
                )
                .limit(1)
            )
        ).first()
        if row is None:
            raise EvaluationBlockedError("durable evaluation approval is missing")
        run, decision = row
        manifest = run.input.get("provider_activation") if isinstance(run.input, dict) else None
        try:
            expires_at = datetime.fromisoformat(str(manifest["expires_at"]).replace("Z", "+00:00"))
            count_policy = manifest["count_policy"]
            count_evidence_expires_at = datetime.fromisoformat(
                str(count_policy["evidence_expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            raise EvaluationBlockedError("evaluation activation manifest is invalid") from None
        if (
            run.status != "succeeded"
            or run.target_type != "rag"
            or run.trigger_source != "manual"
            or run.model != "gpt-6-luna"
            or not run.requested_by_subject
            or decision.decision != "approved"
            or not decision.decided_by_subject
            or not isinstance(decision.comparison_snapshot, dict)
            or decision.comparison_snapshot.get("provider_activation") != manifest
            or not isinstance(manifest, dict)
            or manifest.get("purpose") != ACTIVATION_PURPOSE
            or manifest.get("max_turns") != 50
            or manifest.get("max_attempts") != 100
            or manifest.get("cost_ceiling_usd") != 0.10
            or manifest.get("model") != "gpt-6-luna"
            or manifest.get("service_tier") != "default"
            or manifest.get("max_input_tokens") != 3000
            or manifest.get("max_output_tokens") != 500
            or manifest.get("public_chat_session_id") != public_chat_session_id
            or not isinstance(count_policy, dict)
            or count_policy.get("endpoint")
            != "https://api.openai.com/v1/responses/input_tokens"
            or count_policy.get("model") != "gpt-6-luna"
            or count_policy.get("region") != "standard"
            or count_policy.get("billing_status_claim") != "billable"
            or count_policy.get("evidence_verification") != "independently_verified"
            or count_policy.get("explicitly_approved") is not True
            or not str(count_policy.get("evidence_id", "")).strip()
            or not isinstance(count_policy.get("reservation_per_attempt_usd"), (int, float))
            or isinstance(count_policy.get("reservation_per_attempt_usd"), bool)
            or count_policy["reservation_per_attempt_usd"] <= 0
            or count_policy.get("reservation_all_attempts_usd")
            != round(
                count_policy["reservation_per_attempt_usd"]
                * EvaluationPolicy().max_attempts,
                6,
            )
            or manifest.get("combined_reservation_usd")
            != round(
                EvaluationPolicy().max_attempts
                * (
                    EvaluationPolicy().reserved_cost_per_attempt
                    + count_policy["reservation_per_attempt_usd"]
                ),
                6,
            )
            or runtime_count_policy.endpoint != count_policy["endpoint"]
            or runtime_count_policy.model != count_policy["model"]
            or runtime_count_policy.region != count_policy["region"]
            or runtime_count_policy.reservation_per_attempt_usd
            != count_policy["reservation_per_attempt_usd"]
            or runtime_count_policy.evidence_id != count_policy["evidence_id"]
            or runtime_count_policy.evidence_expires_at.astimezone(UTC)
            != count_evidence_expires_at.astimezone(UTC)
            or runtime_count_policy.independently_verified is not True
            or runtime_count_policy.explicitly_approved is not True
            or count_evidence_expires_at.tzinfo is None
            or count_evidence_expires_at <= datetime.now(UTC)
            or expires_at.tzinfo is None
            or expires_at <= datetime.now(UTC)
        ):
            raise EvaluationBlockedError("evaluation activation is not approved")
        return ApprovedEvaluationActivation(
            run_id=run.run_id,
            organization_id=organization_id,
            public_chat_session_id=public_chat_session_id,
        )


@dataclass(frozen=True)
class PublicChatModelAnswer:
    text: str
    source_ids: tuple[str, ...]
    mocked: bool


class PublicChatModelEvaluationProvider:
    """Compose real tenant context through the bounded evaluation controller."""

    def __init__(
        self,
        *,
        run_id: str,
        count_policy: InputTokenCountPolicy,
        count_transport: ExactInputTokenCountTransport,
        adapter: ProviderDisconnectedResponsesAdapter,
    ) -> None:
        if not run_id:
            raise EvaluationBlockedError("an approved evaluation run id is required")
        self.run_id = run_id
        self.count_policy = count_policy
        self.count_transport = count_transport
        self.adapter = adapter

    async def targets(
        self, db: AsyncSession, *, organization_id: int, session_id: int
    ) -> bool:
        """Return true only for the exact decision-bound synthetic session."""
        try:
            await PersistedEvaluationActivationGuard.require(
                db,
                organization_id=organization_id,
                run_id=self.run_id,
                public_chat_session_id=session_id,
                runtime_count_policy=self.count_policy,
            )
        except EvaluationBlockedError:
            return False
        return True

    async def answer(
        self,
        db: AsyncSession,
        *,
        organization_id: int,
        session_id: int,
        conversation_id: int,
        current_message_id: int,
        question: str,
        assistant_name: str,
        language: str,
    ) -> PublicChatModelAnswer:
        approval = await PersistedEvaluationActivationGuard.require(
            db,
            organization_id=organization_id,
            run_id=self.run_id,
            public_chat_session_id=session_id,
            runtime_count_policy=self.count_policy,
        )
        bound_message = await db.scalar(
            select(ConversationMessage.id)
            .join(
                PublicChatSession,
                (PublicChatSession.conversation_id == ConversationMessage.conversation_id)
                & (PublicChatSession.organization_id == ConversationMessage.organization_id),
            )
            .where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.conversation_id == conversation_id,
                ConversationMessage.id == current_message_id,
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.direction == "inbound",
                ConversationMessage.visibility == "public",
            )
        )
        if bound_message is None:
            raise EvaluationBlockedError(
                "session, conversation, and inbound message are not bound"
            )
        messages = await ConversationMessageRepository.list_recent_public_for_conversation_for_tenant(
            db,
            conversation_id=conversation_id,
            organization_id=organization_id,
            limit=13,
        )
        prior_customer_question = next(
            (
                message.body
                for message in reversed(messages)
                if message.id != current_message_id and message.direction == "inbound"
            ),
            None,
        )
        evidence = await offline_grounded_chat.answer(
            db,
            organization_id=organization_id,
            question=question,
            previous_customer_question=prior_customer_question,
        )
        if evidence is None:
            raise EvaluationBlockedError("tenant knowledge is insufficient")
        chunk_id = int(evidence.source_id.removeprefix("offline:"))
        knowledge_row = (
            await db.execute(
                select(KnowledgeChunk.content, KnowledgeDocument.title)
                .join(
                    KnowledgeDocument,
                    (KnowledgeDocument.id == KnowledgeChunk.document_id)
                    & (KnowledgeDocument.organization_id == KnowledgeChunk.organization_id),
                )
                .where(
                    KnowledgeChunk.id == chunk_id,
                    KnowledgeChunk.organization_id == organization_id,
                    KnowledgeDocument.organization_id == organization_id,
                )
            )
        ).first()
        if knowledge_row is None:
            raise EvaluationBlockedError("tenant knowledge changed before generation")
        history = [
            GroundedTurn(
                "customer" if message.direction == "inbound" else "assistant",
                message.body,
            )
            for message in messages
            if message.id != current_message_id
        ][-12:]
        turn_number = int(
            await db.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.organization_id == organization_id,
                    ConversationMessage.direction == "inbound",
                    ConversationMessage.visibility == "public",
                    ConversationMessage.id <= current_message_id,
                )
            )
            or 0
        )
        if turn_number < 1 or turn_number > EvaluationPolicy().max_turns:
            raise EvaluationBlockedError("approved session turn cap exceeded")
        serialized_input = self.adapter.serialize_grounded_input(
            session_status="ai_active",
            assistant_name=assistant_name,
            language=language,
            history=history,
            knowledge=[(evidence.source_id, knowledge_row.content)],
            customer_message=question,
        )
        request_payload = self.adapter.request_payload(serialized_input)
        controller = ModelEvaluationController(
            policy=EvaluationPolicy(),
            exact_token_counter=None,
            count_policy=self.count_policy,
            count_transport=self.count_transport,
            transport=DisconnectedResponsesEvaluationTransport(self.adapter),
            explicitly_approved=True,
            approved_run_id=approval.run_id,
            approved_organization_id=approval.organization_id,
            session_guard=SnapshotSessionGuard(),
            development_provider_source_enabled=not self.adapter.transport.provider_disconnected,
            environment="development",
        )
        raw = await controller.attempt(
            db,
            organization_id=organization_id,
            run_id=approval.run_id,
            turn_number=turn_number,
            retry_number=0,
            serialized_input=serialized_input,
            public_chat_session_id=session_id,
            canonical_request_payload=request_payload,
        )
        parsed = json.loads(raw)
        if parsed.get("refused") is True:
            raise EvaluationBlockedError("model refused the grounded answer")
        source_ids = tuple(parsed.get("citation_ids") or ())
        if source_ids != (evidence.source_id,):
            raise EvaluationBlockedError("model citations do not match retrieved evidence")
        return PublicChatModelAnswer(
            text=str(parsed["answer"]),
            source_ids=source_ids,
            mocked=self.adapter.transport.provider_disconnected,
        )
