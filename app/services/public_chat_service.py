"""Public web-chat orchestration (Phase 1P.1).

A visitor's message lands in a tenant-owned ticket + conversation, then the
existing Phase 1K agent workflow runs with ``authz=None``. That choice is the
security keystone of the whole channel: ``AgentWorkflowService`` forces
``auto_queued = False`` without an ``AuthorizationContext``, local (non-Zendesk)
chat tickets cannot enqueue external execution, and ``agent_execution_service``
refuses to run any run whose status is not ``approved`` (which only a human can
set). Customer text — including prompt-injection attempts — can therefore never
authorize a tool.

Phase 1P.1 is a handoff-only intake channel: under the repo's tool
authorization policy every substantive decision (``respond`` / ``route`` /
``escalate`` / ``human_review``) requires human approval, so the widget never
returns live model text. Only no-op decisions return the fixed fallback text;
the agent's draft stays in the run for staff. Publishing a freed agent draft
to customers is an explicit Phase 1P.2 carve-out.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.models.ticket import Ticket
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.public_chat_repository import PublicChatRepository
from app.repositories.ticket_repository import TicketRepository
from app.services.agent_workflow_service import agent_workflow_service
from app.services.conversation_ingestion_service import ConversationIngestionService
from app.services.public_chat_rate_limiter import public_chat_rate_limiter_db
from app.services.ticket_routing_service import TicketRoutingService

LOCAL_PROVIDER = "cxops"
WEB_CHANNEL = "web"

# Public widget key prefix (cosmetic distinction from machine-channel keys).
PUBLIC_WIDGET_KEY_PREFIX = "pk_live_"

DEDUPE_INBOUND_PREFIX = "public_chat:"
DEDUPE_REPLY_PREFIX = "public_chat_reply:"
MAX_HISTORY_MESSAGES = 50

TICKET_PLACEHOLDER_SUBJECT = "Web chat started"

# Fixed, bounded handoff text. No response-time promises; no internal state.
HANDOFF_REPLY = "A support team member needs to review this request."
FALLBACK_REPLY = "Your request has been received by the CXOps team."

# Handoff text for a tenant whose business provider runs in local-demo mode.
# A local-demo tenant has no humans reviewing anything: nothing is dispatched to
# A1, so promising a person will look at the request is as false as promising a
# quote. The A1 adapter's wording contract states plainly that no human reviews
# these requests during the pilot, and the widget must not contradict it. This
# copy describes only the one real effect -- the system recorded the request.
LOCAL_DEMO_HANDOFF_REPLY = (
    "This request has been recorded by the CXOps pilot. No one has been "
    "contacted, and no booking, quote, offer, or valuation has been made."
)

# Decisions that never touch a customer-facing tool. When one of these
# completes there is nothing to escalate to staff, so the widget shows the
# fixed fallback instead of a handoff. Every other action is handoff-only.
SAFE_AUTOREPLY_ACTIONS = frozenset({"no_action", "internal_note"})


class PublicChatConfigurationNotFoundError(Exception):
    """No enabled configuration exists for the supplied widget key."""


class PublicChatWidgetDisabledError(Exception):
    """The configuration exists but is disabled."""


class PublicChatOriginNotAllowedError(Exception):
    """The embedding origin is not on the exact-match allowlist."""


class PublicChatSessionNotFoundError(Exception):
    """No session matches the supplied token (or the token is malformed)."""


class PublicChatSessionExpiredError(Exception):
    """The session credential has expired."""


class PublicChatSessionClosedError(Exception):
    """The session is closed."""


class PublicChatRateLimitedError(Exception):
    """A rate or concurrency guard rejected the request."""


class PublicChatInputTooLongError(Exception):
    """The message exceeds the configured maximum length."""


class PublicChatMessageInFlightError(Exception):
    """A duplicate send is still being processed by another request."""


def hash_digest(value: str) -> str:
    """Deterministic SHA-256 hex digest used for all stored secrets."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


log = get_logger(__name__)


class PublicChatService:
    @staticmethod
    async def _maybe_prune_rate_buckets(db: AsyncSession) -> None:
        """Opportunistically prune stale rate-limit buckets; never blocks."""
        try:
            await public_chat_rate_limiter_db.prune(db)
        except Exception:
            log.exception("public_chat_rate_bucket_prune_failed")

    @staticmethod
    def generate_session_token() -> str:
        return secrets.token_urlsafe(settings.public_chat_session_token_entropy_bytes)

    @classmethod
    async def handoff_reply_for(
        cls,
        db: AsyncSession,
        organization_id: int,
    ) -> str:
        """Return the handoff text this tenant's configuration permits.

        A local-demo provider dispatches nothing, so telling the customer a
        person will review the request would be a false promise. Default to the
        generic text and only narrow it when the tenant actually declares a
        local-demo business provider.
        """
        try:
            from app.services.business_integration_service import (
                BusinessIntegrationService,
            )

            rows = await BusinessIntegrationService.list_configs(
                db, organization_id
            )
        except Exception:  # pragma: no cover - defensive
            return HANDOFF_REPLY

        for row in rows:
            mode = (row.config_json or {}).get("provider_mode")
            if mode == "local_demo":
                return LOCAL_DEMO_HANDOFF_REPLY
        return HANDOFF_REPLY

    @staticmethod
    async def resolve_config(
        db: AsyncSession,
        public_widget_key: str,
    ) -> PublicChatConfiguration:
        """Resolve the tenant-owned configuration from the widget key.

        Only the SHA-256 digest of the key is ever matched; the raw key is
        public by design and never stored. No organization id is accepted from
        the client at any point.
        """
        configuration = await PublicChatRepository.get_config_by_key_hash(
            db,
            public_widget_key_hash=hash_digest(public_widget_key),
        )
        if configuration is None:
            raise PublicChatConfigurationNotFoundError(
                "The widget configuration was not found."
            )
        if not configuration.enabled:
            raise PublicChatWidgetDisabledError("The widget is disabled.")
        return configuration

    @staticmethod
    def validate_embedding_origin(
        configuration: PublicChatConfiguration,
        embedding_origin: str | None,
    ) -> None:
        """Exact-match allowlist check for the embedding origin.

        ``allowed_origins`` holds whole origins (scheme://host[:port]) and
        matching is byte-for-byte — no substring or prefix matching. An empty
        allowlist denies everything (fail closed).
        """
        allowed = configuration.allowed_origins or []
        if embedding_origin is None or embedding_origin not in allowed:
            raise PublicChatOriginNotAllowedError(
                "The widget is not enabled for this origin."
            )

    @staticmethod
    async def verify_session(
        db: AsyncSession,
        session_token: str,
    ) -> PublicChatSession:
        """Resolve a session from its token, rejecting expired/closed sessions."""
        session = await PublicChatRepository.get_session_by_token_hash(
            db,
            token_hash=hash_digest(session_token),
        )
        if session is None:
            raise PublicChatSessionNotFoundError("Session not found.")
        if session.is_expired:
            raise PublicChatSessionExpiredError("Session expired.")
        if session.status == "closed":
            raise PublicChatSessionClosedError("Session closed.")
        return session

    @classmethod
    async def create_session(
        cls,
        db: AsyncSession,
        public_widget_key: str,
        embedding_origin: str | None,
    ) -> dict:
        """Create the session, its ticket, its conversation, and the token.

        The token is generated here and returned exactly once; only its digest
        is persisted. The ticket uses ``source="web-chat"`` (free-text column,
        no schema change) and its subject/description are refined from the first
        message.
        """
        configuration = await cls.resolve_config(db, public_widget_key)
        cls.validate_embedding_origin(configuration, embedding_origin)

        await cls._maybe_prune_rate_buckets(db)
        if not await public_chat_rate_limiter_db.allow(
            db,
            scope="public_chat_sessions_per_hour",
            key=f"config:{configuration.id}",
            limit=settings.public_chat_new_sessions_per_hour_per_config,
            window_seconds=3600.0,
        ):
            raise PublicChatRateLimitedError(
                "Too many sessions for this widget. Please try again later."
            )

        active = await PublicChatRepository.count_active_sessions_for_config(
            db,
            configuration_id=configuration.id,
            now=datetime.now(UTC),
        )
        if active >= settings.public_chat_max_active_sessions_per_config:
            raise PublicChatRateLimitedError(
                "The widget is at capacity. Please try again later."
            )

        ticket = Ticket(
            subject=TICKET_PLACEHOLDER_SUBJECT,
            description="",
            source="web-chat",
            priority="normal",
            customer_id=None,
            organization_id=configuration.organization_id,
        )
        created_ticket = await TicketRepository.create(db, ticket)
        await TicketRoutingService.route_new_ticket(db, created_ticket)
        await db.commit()

        conversation = Conversation(
            organization_id=configuration.organization_id,
            customer_id=None,
            ticket_id=created_ticket.id,
            provider=LOCAL_PROVIDER,
            channel=WEB_CHANNEL,
            external_thread_id=None,
            subject=TICKET_PLACEHOLDER_SUBJECT,
            status="open",
        )
        await ConversationRepository.create(db, conversation)

        token = cls.generate_session_token()
        session = PublicChatSession(
            organization_id=configuration.organization_id,
            configuration_id=configuration.id,
            conversation_id=conversation.id,
            ticket_id=created_ticket.id,
            customer_id=None,
            token_hash=hash_digest(token),
            status="ai_active",
            expires_at=datetime.now(UTC)
            + timedelta(hours=configuration.session_ttl_hours),
        )
        created_session = await PublicChatRepository.create_session(db, session)

        return {
            "configuration": configuration,
            "session": created_session,
            "token": token,
        }

    @classmethod
    async def _insert_inbound_message(
        cls,
        db: AsyncSession,
        *,
        conversation: Conversation,
        text: str,
        client_message_id: str,
    ) -> ConversationMessage:
        dedupe_key = f"{DEDUPE_INBOUND_PREFIX}{client_message_id}"
        existing = (
            await ConversationMessageRepository.get_by_dedupe_key(
                db,
                conversation_id=conversation.id,
                organization_id=conversation.organization_id,
                dedupe_key=dedupe_key,
            )
        )
        if existing is not None:
            return existing

        message = ConversationMessage(
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            provider=LOCAL_PROVIDER,
            dedupe_key=dedupe_key,
            direction="inbound",
            visibility="public",
            body=text,
            sent_at=datetime.now(UTC),
        )
        try:
            return await ConversationMessageRepository.create(db, message)
        except IntegrityError:
            await db.rollback()
            re_resolved = (
                await ConversationMessageRepository.get_by_dedupe_key(
                    db,
                    conversation_id=conversation.id,
                    organization_id=conversation.organization_id,
                    dedupe_key=dedupe_key,
                )
            )
            if re_resolved is None:
                raise
            return re_resolved

    @classmethod
    async def _insert_reply_message(
        cls,
        db: AsyncSession,
        *,
        conversation: Conversation,
        body: str,
        client_message_id: str,
    ) -> ConversationMessage | None:
        dedupe_key = f"{DEDUPE_REPLY_PREFIX}{client_message_id}"
        existing = (
            await ConversationMessageRepository.get_by_dedupe_key(
                db,
                conversation_id=conversation.id,
                organization_id=conversation.organization_id,
                dedupe_key=dedupe_key,
            )
        )
        if existing is not None:
            return existing

        message = ConversationMessage(
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            provider=LOCAL_PROVIDER,
            dedupe_key=dedupe_key,
            direction="outbound",
            visibility="public",
            body=body,
            sent_at=datetime.now(UTC),
        )
        try:
            return await ConversationMessageRepository.create(db, message)
        except IntegrityError:
            await db.rollback()
            return (
                await ConversationMessageRepository.get_by_dedupe_key(
                    db,
                    conversation_id=conversation.id,
                    organization_id=conversation.organization_id,
                    dedupe_key=dedupe_key,
                )
            )

    @classmethod
    async def _mirror_first_message_to_ticket(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        text: str,
    ) -> None:
        """Refine the placeholder ticket from the first customer message."""
        organization_id = ticket.organization_id
        if organization_id is None:
            raise ValueError("Ticket must be tenant-owned")
        if ticket.subject != TICKET_PLACEHOLDER_SUBJECT:
            return
        await TicketRepository.update_for_tenant(
            db,
            ticket=ticket,
            changes={
                "subject": _truncate(text, 255),
                "description": text,
            },
            organization_id=organization_id,
        )
        await ConversationIngestionService.sync_ticket_conversation(
            db,
            ticket=ticket,
            organization_id=organization_id,
        )

    @classmethod
    async def send_message(
        cls,
        db: AsyncSession,
        *,
        session_token: str,
        embedding_origin: str | None,
        text: str,
        client_message_id: str,
    ) -> dict:
        """Append an inbound message and run the agent (``authz=None``).

        Idempotent by ``client_message_id``: a retry resolves the already
        persisted inbound + reply messages and returns them, and the reply is
        keyed by the same id so the returned text is stable.
        """
        session = await cls.verify_session(db, session_token)
        configuration = await PublicChatRepository.get_config_by_id_for_tenant(
            db,
            configuration_id=session.configuration_id,
            organization_id=session.organization_id,
        )
        if configuration is None:
            raise PublicChatConfigurationNotFoundError(
                "The widget configuration was not found."
            )
        cls.validate_embedding_origin(configuration, embedding_origin)

        if len(text) > configuration.max_message_length:
            raise PublicChatInputTooLongError(
                f"Messages are limited to {configuration.max_message_length} characters."
            )

        await cls._maybe_prune_rate_buckets(db)
        if not await public_chat_rate_limiter_db.allow(
            db,
            scope="session_messages",
            key=f"session:{session.id}",
            limit=configuration.max_messages_per_minute,
            window_seconds=60.0,
        ):
            raise PublicChatRateLimitedError(
                "You are sending messages too quickly. Please wait a moment."
            )
        if not await public_chat_rate_limiter_db.allow(
            db,
            scope="config_messages",
            key=f"config:{configuration.id}",
            limit=settings.public_chat_max_messages_per_minute_per_config,
            window_seconds=60.0,
        ):
            raise PublicChatRateLimitedError(
                "The widget is busy. Please try again in a moment."
            )

        conversation_result = await db.execute(
            select(Conversation).where(
                Conversation.id == session.conversation_id,
                Conversation.organization_id == session.organization_id,
            )
        )
        conversation = conversation_result.scalar_one_or_none()
        if conversation is None:
            raise PublicChatSessionNotFoundError("Session conversation not found.")

        # ---- early duplicate: retry returns the already-persisted reply ----
        inbound = await ConversationMessageRepository.get_by_dedupe_key(
            db,
            conversation_id=conversation.id,
            organization_id=conversation.organization_id,
            dedupe_key=f"{DEDUPE_INBOUND_PREFIX}{client_message_id}",
        )
        if inbound is not None:
            reply = await ConversationMessageRepository.get_by_dedupe_key(
                db,
                conversation_id=conversation.id,
                organization_id=conversation.organization_id,
                dedupe_key=f"{DEDUPE_REPLY_PREFIX}{client_message_id}",
            )
            if reply is not None:
                handoff_text = await cls.handoff_reply_for(
                    db, session.organization_id
                )
                return {
                    "message_id": inbound.id,
                    "reply": reply.body,
                    "status": session.status,
                    "handoff": reply.body == handoff_text,
                }
            raise PublicChatMessageInFlightError(
                "This message is still being processed."
            )

        inbound = await cls._insert_inbound_message(
            db,
            conversation=conversation,
            text=text,
            client_message_id=client_message_id,
        )

        ticket_result = await db.execute(
            select(Ticket).where(
                Ticket.id == session.ticket_id,
                Ticket.organization_id == session.organization_id,
            )
        )
        ticket = ticket_result.scalar_one_or_none()
        if ticket is None:
            raise PublicChatSessionNotFoundError("Session ticket not found.")

        if ticket.subject == TICKET_PLACEHOLDER_SUBJECT:
            await cls._mirror_first_message_to_ticket(
                db,
                ticket=ticket,
                text=text,
            )
        else:
            await ConversationRepository.update_for_tenant(
                db,
                conversation=conversation,
                changes={"latest_message_at": datetime.now(UTC)},
                organization_id=conversation.organization_id,
            )

        organization_id = session.organization_id
        result = await agent_workflow_service.analyze(
            db=db,
            ticket_id=ticket.id,
            organization_id=organization_id,
            allow_auto_queue=True,
            persist_run=True,
            authz=None,
        )

        # ---- bounded reply selection: handoff-only (Phase 1P.1) ----
        # The repo's tool policy forces requires_human_approval on every
        # customer-facing action (respond/route/escalate), so those runs are
        # escalated to staff and the widget shows fixed text — never live model
        # output. Only no-op decisions complete the turn with the fallback.
        handoff = False
        reply_text = FALLBACK_REPLY
        run = await AgentRunRepository.get_by_run_id_for_tenant(
            db,
            run_id=result["run_id"],
            organization_id=organization_id,
        )
        if run is None or run.action not in SAFE_AUTOREPLY_ACTIONS:
            handoff = True
            reply_text = await cls.handoff_reply_for(db, organization_id)

        if handoff and session.status == "ai_active":
            session = await PublicChatRepository.update_session_for_tenant(
                db,
                session=session,
                changes={"status": "human_requested"},
                organization_id=session.organization_id,
            )

        await cls._insert_reply_message(
            db,
            conversation=conversation,
            body=reply_text,
            client_message_id=client_message_id,
        )

        # The identifiers are logged, never returned: the public response schema
        # is deliberately free of anything a caller could use to address another
        # tenant's session, but an operator still needs to find this exact
        # conversation when a pilot customer reports a bad reply.
        log.info(
            "public_chat_reply_sent",
            organization_id=session.organization_id,
            session_id=session.id,
            message_id=inbound.id,
            status=session.status,
            handoff=handoff,
        )

        return {
            "message_id": inbound.id,
            "reply": reply_text,
            "status": session.status,
            "handoff": handoff,
        }

    @staticmethod
    async def get_state(
        db: AsyncSession,
        session: PublicChatSession,
    ) -> dict:
        messages = await PublicChatRepository.list_public_messages(
            db,
            conversation_id=session.conversation_id,
            organization_id=session.organization_id,
            limit=MAX_HISTORY_MESSAGES,
        )
        return {
            "status": session.status,
            "expires_at": session.expires_at,
            "closed_at": session.closed_at,
            "messages": [
                {
                    "id": message.id,
                    "direction": message.direction,
                    "body": message.body,
                    "sent_at": message.sent_at,
                }
                for message in messages
            ],
        }

    @classmethod
    async def request_human(
        cls,
        db: AsyncSession,
        session: PublicChatSession,
    ) -> dict:
        if session.status == "closed":
            raise PublicChatSessionClosedError("Session closed.")
        if session.status == "ai_active":
            session = await PublicChatRepository.update_session_for_tenant(
                db,
                session=session,
                changes={"status": "human_requested"},
                organization_id=session.organization_id,
            )
        return {
            "status": session.status,
            "reply": await cls.handoff_reply_for(db, session.organization_id),
        }

    @staticmethod
    async def close_session(
        db: AsyncSession,
        session: PublicChatSession,
    ) -> dict:
        if session.status != "closed":
            session = await PublicChatRepository.update_session_for_tenant(
                db,
                session=session,
                changes={"status": "closed", "closed_at": datetime.now(UTC)},
                organization_id=session.organization_id,
            )
        return {"status": session.status}


public_chat_service = PublicChatService()