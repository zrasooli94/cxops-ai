"""Disabled real-model composition for a controlled public-chat evaluation.

No application startup code constructs this provider. When explicitly injected,
it still requires a tenant-owned immutable release decision for an exact bounded
activation manifest. The production configuration rejects the feature flag.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_evaluation_live_authorization import AIEvaluationLiveAuthorization
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.conversation_message import ConversationMessage
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.public_chat import PublicChatSession
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.services.model_eval_conditional_billing import (
    CONDITIONAL_BILLING_POLICY,
    require_current_conditional_billing_manifest,
)
from app.services.model_eval_controller import (
    EvaluationBlockedError,
    EvaluationPolicy,
    ExactInputTokenCountTransport,
    InputTokenCountPolicy,
    ModelEvaluationController,
)
from app.services.model_eval_live_authorization import (
    validate_live_authorization_snapshot,
)
from app.services.model_eval_openai_adapter import (
    DisconnectedResponsesEvaluationTransport,
    GroundedTurn,
    ProviderDisconnectedResponsesAdapter,
)
from app.services.model_eval_rag_prerequisite import (
    validate_persisted_offline_rag_contract,
)
from app.services.offline_grounded_chat import offline_grounded_chat

ACTIVATION_PURPOSE = "controlled_public_chat_model_evaluation"


def validate_activation_manifest(manifest: dict) -> dict:
    """Validate the bounded manifest an authenticated manager reviewed."""
    if not isinstance(manifest, dict):
        raise EvaluationBlockedError("evaluation activation manifest is invalid")
    manifest_keys = {
        "purpose",
        "frozen_fixture_sha256",
        "evaluation_spec_sha256",
        "runner_source_sha256",
        "reviewed_source_commit",
        "public_chat_session_ids",
        "max_turns",
        "max_attempts",
        "cost_ceiling_usd",
        "model",
        "service_tier",
        "max_input_tokens",
        "max_output_tokens",
        "generation_input_rate_per_million_usd",
        "generation_output_rate_per_million_usd",
        "generation_price_multiplier",
        "generation_reservation_all_attempts_usd",
        "additional_all_in_reserve_usd",
        "all_in_evidence_id",
        "all_in_evidence_expires_at",
        "all_in_evidence_verification",
        "count_policy",
        "combined_reservation_usd",
        "expires_at",
    }
    count_keys = {
        "endpoint",
        "model",
        "region",
        "billing_status_claim",
        "evidence_url",
        "evidence_verification",
        "evidence_id",
        "evidence_expires_at",
        "explicitly_approved",
        "reservation_per_attempt_usd",
        "reservation_all_attempts_usd",
    }
    if frozenset(manifest) not in {
        frozenset(manifest_keys),
        frozenset({*manifest_keys, "conditional_billing_policy"}),
    } or not isinstance(
        manifest.get("count_policy"), dict
    ):
        raise EvaluationBlockedError("evaluation activation manifest is invalid")
    if set(manifest["count_policy"]) != count_keys:
        raise EvaluationBlockedError("evaluation activation manifest is invalid")
    try:
        expiry = datetime.fromisoformat(
            str(manifest["expires_at"]).replace("Z", "+00:00")
        )
        count = manifest["count_policy"]
        count_expiry = datetime.fromisoformat(
            str(count["evidence_expires_at"]).replace("Z", "+00:00")
        )
        all_in_expiry = datetime.fromisoformat(
            str(manifest["all_in_evidence_expires_at"]).replace("Z", "+00:00")
        )
        sessions = manifest["public_chat_session_ids"]
        reservation = count["reservation_per_attempt_usd"]
        additional = manifest["additional_all_in_reserve_usd"]
    except (KeyError, TypeError, ValueError):
        raise EvaluationBlockedError(
            "evaluation activation manifest is invalid"
        ) from None
    billing = count.get("billing_status_claim")
    conditional = manifest.get("conditional_billing_policy") is not None
    fixed = {
        "purpose": ACTIVATION_PURPOSE,
        "max_turns": 50,
        "max_attempts": 100,
        "cost_ceiling_usd": 0.10,
        "model": "gpt-6-luna",
        "service_tier": "default",
        "max_input_tokens": 3000,
        "max_output_tokens": 500,
        "generation_input_rate_per_million_usd": 0.10,
        "generation_output_rate_per_million_usd": 0.50,
        "generation_price_multiplier": 1.10,
        "generation_reservation_all_attempts_usd": 0.0605,
    }
    valid = (
        all(manifest.get(key) == value for key, value in fixed.items())
        and all(
            isinstance(manifest.get(key), str)
            and len(manifest[key]) == length
            and all(char in "0123456789abcdef" for char in manifest[key])
            for key, length in (
                ("frozen_fixture_sha256", 64),
                ("evaluation_spec_sha256", 64),
                ("runner_source_sha256", 64),
                ("reviewed_source_commit", 40),
            )
        )
        and isinstance(sessions, list)
        and len(sessions) == len(set(sessions)) == 10
        and all(
            isinstance(item, int) and not isinstance(item, bool) and item > 0
            for item in sessions
        )
        and count.get("endpoint") == "https://api.openai.com/v1/responses/input_tokens"
        and count.get("model") == "gpt-6-luna"
        and count.get("region") == "standard"
        and billing in {"billable", "no_charge", "unknown"}
        and count.get("evidence_verification")
        in {
            "independently_verified",
            "owner_accepted_uncertainty_not_independently_verified",
        }
        and count.get("explicitly_approved") is True
        and bool(str(count.get("evidence_id", "")).strip())
        and isinstance(reservation, (int, float))
        and not isinstance(reservation, bool)
        and (
            (billing == "billable" and reservation > 0)
            or (billing == "no_charge" and reservation == 0)
            or (
                billing == "unknown"
                and reservation
                == CONDITIONAL_BILLING_POLICY.count_reservation_per_attempt_usd
            )
        )
        and count.get("reservation_all_attempts_usd") == round(reservation * 100, 6)
        and isinstance(additional, (int, float))
        and not isinstance(additional, bool)
        and additional >= 0
        and bool(str(manifest.get("all_in_evidence_id", "")).strip())
        and manifest.get("all_in_evidence_verification")
        in {
            "independently_verified",
            "owner_accepted_planning_assumption_not_independently_verified",
        }
        and manifest.get("combined_reservation_usd")
        == round(0.0605 + reservation * 100 + additional, 6)
        and all(
            item.tzinfo is not None and item > datetime.now(UTC)
            for item in (expiry, count_expiry, all_in_expiry)
        )
    )
    if not valid:
        raise EvaluationBlockedError("evaluation activation manifest is not approved")
    if conditional:
        require_current_conditional_billing_manifest(manifest)
        if (
            billing != "unknown"
            or count.get("evidence_verification")
            != "owner_accepted_uncertainty_not_independently_verified"
            or manifest.get("all_in_evidence_verification")
            != "owner_accepted_planning_assumption_not_independently_verified"
        ):
            raise EvaluationBlockedError(
                "conditional billing manifest is not approved"
            )
    elif (
        count.get("evidence_verification") != "independently_verified"
        or manifest.get("all_in_evidence_verification") != "independently_verified"
    ):
        raise EvaluationBlockedError("evaluation billing evidence is not approved")
    return json.loads(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


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
    public_chat_session_ids: tuple[int, ...]
    manifest: dict


class PersistedEvaluationActivationGuard:
    """Resolve one bounded activation from immutable tenant-owned records."""

    @staticmethod
    async def require(
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
        public_chat_session_id: int,
        runtime_count_policy: InputTokenCountPolicy,
        runtime_evaluation_policy: EvaluationPolicy,
    ) -> ApprovedEvaluationActivation:
        runtime_count_policy.validate(expected_model=runtime_evaluation_policy.model)
        runtime_evaluation_policy.validate()
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
        manifest = (
            decision.comparison_snapshot.get("provider_activation")
            if isinstance(decision.comparison_snapshot, dict)
            else None
        )
        if isinstance(manifest, dict):
            manifest = validate_activation_manifest(manifest)
        try:
            expires_at = datetime.fromisoformat(
                str(manifest["expires_at"]).replace("Z", "+00:00")
            )
            count_policy = manifest["count_policy"]
            count_evidence_expires_at = datetime.fromisoformat(
                str(count_policy["evidence_expires_at"]).replace("Z", "+00:00")
            )
            all_in_evidence_expires_at = datetime.fromisoformat(
                str(manifest["all_in_evidence_expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            raise EvaluationBlockedError(
                "evaluation activation manifest is invalid"
            ) from None
        if (
            run.status != "succeeded"
            or not await validate_persisted_offline_rag_contract(
                db, organization_id=organization_id, run=run
            )
            or not run.requested_by_subject
            or decision.decision != "approved"
            or not decision.decided_by_subject
            or not isinstance(decision.comparison_snapshot, dict)
            or not isinstance(manifest, dict)
            or manifest.get("purpose") != ACTIVATION_PURPOSE
            or manifest.get("max_turns") != 50
            or manifest.get("max_attempts") != 100
            or manifest.get("cost_ceiling_usd") != 0.10
            or manifest.get("model") != "gpt-6-luna"
            or manifest.get("service_tier") != "default"
            or manifest.get("max_input_tokens") != 3000
            or manifest.get("max_output_tokens") != 500
            or manifest.get("generation_input_rate_per_million_usd") != 0.10
            or manifest.get("generation_output_rate_per_million_usd") != 0.50
            or manifest.get("generation_price_multiplier") != 1.10
            or manifest.get("generation_reservation_all_attempts_usd") != 0.0605
            or not isinstance(manifest.get("public_chat_session_ids"), list)
            or len(manifest["public_chat_session_ids"]) != 10
            or len(set(manifest["public_chat_session_ids"])) != 10
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 1
                for item in manifest["public_chat_session_ids"]
            )
            or public_chat_session_id not in manifest["public_chat_session_ids"]
            or not isinstance(count_policy, dict)
            or count_policy.get("endpoint")
            != "https://api.openai.com/v1/responses/input_tokens"
            or count_policy.get("model") != "gpt-6-luna"
            or count_policy.get("region") != "standard"
            or count_policy.get("billing_status_claim")
            not in {"billable", "no_charge", "unknown"}
            or count_policy.get("evidence_verification")
            not in {
                "independently_verified",
                "owner_accepted_uncertainty_not_independently_verified",
            }
            or count_policy.get("explicitly_approved") is not True
            or not str(count_policy.get("evidence_id", "")).strip()
            or not isinstance(
                count_policy.get("reservation_per_attempt_usd"), (int, float)
            )
            or isinstance(count_policy.get("reservation_per_attempt_usd"), bool)
            or (
                count_policy["billing_status_claim"] == "billable"
                and count_policy["reservation_per_attempt_usd"] <= 0
            )
            or (
                count_policy["billing_status_claim"] == "no_charge"
                and count_policy["reservation_per_attempt_usd"] != 0
            )
            or (
                count_policy["billing_status_claim"] == "unknown"
                and count_policy["reservation_per_attempt_usd"]
                != CONDITIONAL_BILLING_POLICY.count_reservation_per_attempt_usd
            )
            or count_policy.get("reservation_all_attempts_usd")
            != round(
                count_policy["reservation_per_attempt_usd"]
                * EvaluationPolicy().max_attempts,
                6,
            )
            or not isinstance(
                manifest.get("additional_all_in_reserve_usd"), (int, float)
            )
            or isinstance(manifest.get("additional_all_in_reserve_usd"), bool)
            or manifest["additional_all_in_reserve_usd"] < 0
            or manifest.get("combined_reservation_usd")
            != round(
                EvaluationPolicy().max_attempts
                * (
                    EvaluationPolicy().reserved_cost_per_attempt
                    + count_policy["reservation_per_attempt_usd"]
                )
                + manifest["additional_all_in_reserve_usd"],
                6,
            )
            or not str(manifest.get("all_in_evidence_id", "")).strip()
            or manifest.get("all_in_evidence_verification")
            not in {
                "independently_verified",
                "owner_accepted_planning_assumption_not_independently_verified",
            }
            or runtime_evaluation_policy.additional_all_in_reserve_usd
            != manifest["additional_all_in_reserve_usd"]
            or runtime_count_policy.endpoint != count_policy["endpoint"]
            or runtime_count_policy.model != count_policy["model"]
            or runtime_count_policy.region != count_policy["region"]
            or runtime_count_policy.reservation_per_attempt_usd
            != count_policy["reservation_per_attempt_usd"]
            or runtime_count_policy.billing_status
            != count_policy["billing_status_claim"]
            or runtime_count_policy.evidence_id != count_policy["evidence_id"]
            or runtime_count_policy.evidence_expires_at.astimezone(UTC)
            != count_evidence_expires_at.astimezone(UTC)
            or runtime_count_policy.independently_verified
            != (
                count_policy.get("evidence_verification") == "independently_verified"
            )
            or runtime_count_policy.conditional_policy_version
            != (
                CONDITIONAL_BILLING_POLICY.version
                if manifest.get("conditional_billing_policy") is not None
                else None
            )
            or runtime_count_policy.conditional_evidence_sha256
            != (
                CONDITIONAL_BILLING_POLICY.support_evidence_sha256
                if manifest.get("conditional_billing_policy") is not None
                else None
            )
            or runtime_count_policy.explicitly_approved is not True
            or count_evidence_expires_at.tzinfo is None
            or count_evidence_expires_at <= datetime.now(UTC)
            or all_in_evidence_expires_at.tzinfo is None
            or all_in_evidence_expires_at <= datetime.now(UTC)
            or expires_at.tzinfo is None
            or expires_at <= datetime.now(UTC)
        ):
            raise EvaluationBlockedError("evaluation activation is not approved")
        if manifest.get("conditional_billing_policy") is not None:
            require_current_conditional_billing_manifest(manifest)
            if (
                manifest["conditional_billing_policy"].get("accepted_by_subject")
                != decision.decided_by_subject
            ):
                raise EvaluationBlockedError(
                    "conditional billing acceptance subject is invalid"
                )
        live_authorization = await db.scalar(
            select(AIEvaluationLiveAuthorization)
            .where(
                AIEvaluationLiveAuthorization.organization_id == organization_id,
                AIEvaluationLiveAuthorization.candidate_run_id == run.id,
            )
            .order_by(
                AIEvaluationLiveAuthorization.created_at.desc(),
                AIEvaluationLiveAuthorization.id.desc(),
            )
            .limit(1)
        )
        if (
            live_authorization is None
            or live_authorization.decision != "authorized"
            or live_authorization.release_decision_id != decision.id
        ):
            raise EvaluationBlockedError(
                "latest live evaluation authorization is not authorized"
            )
        validate_live_authorization_snapshot(
            live_authorization.authorization_snapshot,
            manifest=manifest,
            release_decision_id=decision.id,
            authorized_by_subject=live_authorization.authorized_by_subject,
            expires_at=live_authorization.expires_at,
        )
        return ApprovedEvaluationActivation(
            run_id=run.run_id,
            organization_id=organization_id,
            public_chat_session_ids=tuple(manifest["public_chat_session_ids"]),
            manifest=json.loads(json.dumps(manifest, ensure_ascii=False, sort_keys=True)),
        )


@dataclass(frozen=True)
class PublicChatModelAnswer:
    text: str
    source_ids: tuple[str, ...]
    mocked: bool
    request_id: str


class PublicChatModelEvaluationProvider:
    """Compose real tenant context through the bounded evaluation controller."""

    def __init__(
        self,
        *,
        run_id: str,
        count_policy: InputTokenCountPolicy,
        count_transport: ExactInputTokenCountTransport,
        adapter: ProviderDisconnectedResponsesAdapter,
        evaluation_policy: EvaluationPolicy | None = None,
    ) -> None:
        if not run_id:
            raise EvaluationBlockedError("an approved evaluation run id is required")
        self.run_id = run_id
        self.count_policy = count_policy
        self.count_transport = count_transport
        self.adapter = adapter
        self.evaluation_policy = evaluation_policy or EvaluationPolicy()

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
                runtime_evaluation_policy=self.evaluation_policy,
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
            runtime_evaluation_policy=self.evaluation_policy,
        )
        bound_message = await db.scalar(
            select(ConversationMessage.id)
            .join(
                PublicChatSession,
                (
                    PublicChatSession.conversation_id
                    == ConversationMessage.conversation_id
                )
                & (
                    PublicChatSession.organization_id
                    == ConversationMessage.organization_id
                ),
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
                    & (
                        KnowledgeDocument.organization_id
                        == KnowledgeChunk.organization_id
                    ),
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
        if turn_number < 1 or turn_number > 5:
            raise EvaluationBlockedError(
                "approved conversation requires exactly five turns"
            )
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
            policy=self.evaluation_policy,
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
            raise EvaluationBlockedError(
                "model citations do not match retrieved evidence"
            )
        request_id = "eval-" + hashlib.sha256(
            f"{approval.run_id}:{organization_id}:{session_id}:{turn_number}".encode()
        ).hexdigest()[:48]
        return PublicChatModelAnswer(
            text=str(parsed["answer"]),
            source_ids=source_ids,
            mocked=self.adapter.transport.provider_disconnected,
            request_id=request_id,
        )
