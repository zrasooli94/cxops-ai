from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.zendesk.client import zendesk_client
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.ticket import Ticket
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.ticket_repository import TicketRepository
from app.services.ticket_sla_service import TicketSLAService

log = get_logger("conversation_ingestion")

LOCAL_PROVIDER = "cxops"
ZENDESK_PROVIDER = "zendesk"
TICKET_CHANNEL = "ticket"

# Bounded inbound classification result for a Zendesk comment.
RESOLVED_TICKET_STATUSES = ("solved", "closed")

MAX_ZENDESK_COMMENTS = 100


def _as_datetime_or_none(value) -> datetime | None:
    """Coerce a provider timestamp into a tz-aware datetime, or None.

    Zendesk returns ISO-8601 strings (``2026-09-01T10:00:00Z``), which asyncpg
    rejects as raw strings for a timestamptz column. Ingestion parses before
    insert; a malformed timestamp degrades to ``None`` (chronology loss, never
    a crashed webhook/sync) instead of failing the whole thread.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            log.warning(
                "conversation_comment_invalid_timestamp",
                raw=value[:80],
            )
            return None
    return None


def conversation_status_for_ticket(ticket: Ticket) -> str:
    """A ticket that is resolved/closed closes its conversation; no other
    ticket state is conflated (a re-opened ticket flips back to open)."""
    if ticket.status in RESOLVED_TICKET_STATUSES:
        return "closed"
    return "open"


class ConversationIngestionService:
    """Creates and keeps conversation rows aligned with tickets.

    Ingestion is provably idempotent: every message carries either the
    provider's external id or a deterministic local dedupe key, both backed by
    database unique constraints, so a webhook replay, a retried agent run, or a
    concurrent sync can never double-insert a message or misclassify a thread.
    """

    @classmethod
    async def sync_ticket_conversation(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        organization_id: int,
    ) -> Conversation:
        """Ensure the canonical conversation for a ticket and mirror subject,
        status and customer/link changes onto it.

        Local tickets map to provider ``cxops`` with a NULL external thread;
        Zendesk tickets map to provider ``zendesk`` keyed by their external id.
        """
        if ticket.organization_id != organization_id:
            raise ValueError("Ticket does not belong to this organization")

        is_zendesk = ticket.source == "zendesk"
        provider = ZENDESK_PROVIDER if is_zendesk else LOCAL_PROVIDER
        external_thread_id = ticket.external_id if is_zendesk else None

        conversation = None
        if is_zendesk and external_thread_id is not None:
            conversation = (
                await ConversationRepository.get_by_external_thread_for_tenant(
                    db,
                    provider=provider,
                    external_thread_id=external_thread_id,
                    organization_id=organization_id,
                )
            )
        if conversation is None:
            conversation = await ConversationRepository.get_by_ticket_for_tenant(
                db,
                ticket_id=ticket.id,
                organization_id=organization_id,
            )

        if conversation is None:
            conversation = Conversation(
                organization_id=organization_id,
                customer_id=ticket.customer_id,
                ticket_id=ticket.id,
                provider=provider,
                channel=TICKET_CHANNEL,
                external_thread_id=external_thread_id,
                subject=ticket.subject,
                status=conversation_status_for_ticket(ticket),
            )
            try:
                return await ConversationRepository.create(db, conversation)
            except IntegrityError:
                await db.rollback()
                # A concurrent sync created the thread; re-resolve instead of
                # erroring (mirrors the customer-identity race pattern).
                re_resolved = await cls._resolve_conversation(
                    db,
                    conversation=conversation,
                    is_zendesk=is_zendesk,
                    organization_id=organization_id,
                )
                if re_resolved is not None:
                    conversation = re_resolved
                else:
                    raise

        changes: dict = {
            "subject": ticket.subject,
            "status": conversation_status_for_ticket(ticket),
            "customer_id": ticket.customer_id,
            "ticket_id": ticket.id,
        }
        if (
            conversation.external_thread_id is None
            and external_thread_id is not None
        ):
            changes["external_thread_id"] = external_thread_id

        return await ConversationRepository.update_for_tenant(
            db,
            conversation=conversation,
            changes=changes,
            organization_id=organization_id,
        )

    @staticmethod
    async def _resolve_conversation(
        db: AsyncSession,
        *,
        conversation: Conversation,
        is_zendesk: bool,
        organization_id: int,
    ) -> Conversation | None:
        provider = ZENDESK_PROVIDER if is_zendesk else LOCAL_PROVIDER
        if conversation.external_thread_id is not None:
            found = await ConversationRepository.get_by_external_thread_for_tenant(
                db,
                provider=provider,
                external_thread_id=conversation.external_thread_id,
                organization_id=organization_id,
            )
            if found is not None:
                return found
        if conversation.ticket_id is not None:
            return await ConversationRepository.get_by_ticket_for_tenant(
                db,
                ticket_id=conversation.ticket_id,
                organization_id=organization_id,
            )
        return None

    @classmethod
    async def ingest_initial_message(
        cls,
        db: AsyncSession,
        *,
        conversation: Conversation,
        ticket: Ticket,
        organization_id: int,
    ) -> ConversationMessage:
        """Deterministic inbound message created from the ticket description.

        Used at local-ticket creation and as the idempotent fallback when a
        Zendesk ticket has no retrievable comments. ``sent_at`` is the ticket
        creation time so the thread reads chronologically.
        """
        dedupe_key = f"ticket_initial:{ticket.id}"

        existing = await ConversationMessageRepository.get_by_dedupe_key(
            db,
            conversation_id=conversation.id,
            organization_id=organization_id,
            dedupe_key=dedupe_key,
        )
        if existing is not None:
            return existing

        body = (
            ticket.description or ticket.subject or "No description"
        )

        message = ConversationMessage(
            organization_id=organization_id,
            conversation_id=conversation.id,
            provider=conversation.provider,
            dedupe_key=dedupe_key,
            direction="inbound",
            visibility="public",
            body=body,
            sent_at=ticket.created_at,
        )

        try:
            message = await ConversationMessageRepository.create(db, message)
        except IntegrityError:
            await db.rollback()
            existing = await ConversationMessageRepository.get_by_dedupe_key(
                db,
                conversation_id=conversation.id,
                organization_id=organization_id,
                dedupe_key=dedupe_key,
            )
            if existing is not None:
                return existing
            raise

        sent_at = message.sent_at or message.created_at
        if (
            conversation.latest_message_at is None
            or sent_at > conversation.latest_message_at
        ):
            await ConversationRepository.update_for_tenant(
                db,
                conversation=conversation,
                changes={"latest_message_at": sent_at},
                organization_id=organization_id,
            )

        return message

    @classmethod
    async def sync_conversation_latest_message(
        cls,
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
        sent_at,
    ) -> None:
        conversation = await ConversationRepository.get_by_id_for_tenant(
            db,
            conversation_id=conversation_id,
            organization_id=organization_id,
        )
        if conversation is None:
            return

        if (
            conversation.latest_message_at is None
            or sent_at is None
            or conversation.latest_message_at < sent_at
        ):
            await ConversationRepository.update_for_tenant(
                db,
                conversation=conversation,
                changes={"latest_message_at": sent_at},
                organization_id=organization_id,
            )

    @classmethod
    async def ingest_zendesk_ticket(
        cls,
        db: AsyncSession,
        *,
        zendesk_ticket_id: int,
        organization_id: int,
        comments_response: dict | None = None,
        requester_id: int | None = None,
    ) -> tuple[Conversation, dict]:
        """Bound, classified comment sync for a Zendesk ticket.

        Called from the ONLY Zendesk comment ingestion paths (webhook or the
        manual sync route). Comment authors are classified against the
        requester: public requester comments are inbound, public agent comments
        outbound, private comments internal, and a public comment with an
        unverifiable author degrades to internal (fail safe). When the ticket
        has no comments the deterministic description fallback message is used.
        The fetched comments response is returned so callers (e.g. the webhook
        write-back marker check) can reuse it instead of re-fetching.
        """
        local_ticket = await TicketRepository.get_by_external_id_for_tenant(
            db,
            external_id=str(zendesk_ticket_id),
            organization_id=organization_id,
        )
        if local_ticket is None:
            raise ValueError("Ticket not found in this organization")

        conversation = await cls.sync_ticket_conversation(
            db,
            ticket=local_ticket,
            organization_id=organization_id,
        )

        if requester_id is None:
            ticket_response = await zendesk_client.get_ticket(
                db,
                ticket_id=zendesk_ticket_id,
                organization_id=organization_id,
            )
            requester_id = ticket_response["ticket"].get("requester_id")

        if comments_response is None:
            comments_response = await zendesk_client.get_ticket_comments(
                db,
                ticket_id=zendesk_ticket_id,
                organization_id=organization_id,
            )
        comments = comments_response.get("comments") or []

        if not comments:
            await cls.ingest_initial_message(
                db,
                conversation=conversation,
                ticket=local_ticket,
                organization_id=organization_id,
            )
            return conversation, comments_response

        candidates: list[ConversationMessage] = []
        latest_sent_at = None
        for comment in comments[-MAX_ZENDESK_COMMENTS:]:
            comment_id = comment.get("id")
            if comment_id is None:
                log.warning(
                    "zendesk_comment_missing_id",
                    zendesk_ticket_id=zendesk_ticket_id,
                )
                continue

            external_message_id = str(comment_id)
            public = bool(comment.get("public", True))
            author_id = comment.get("author_id")

            if not public:
                direction, visibility = "internal", "internal"
            elif author_id is not None and requester_id is not None and author_id == requester_id:
                direction, visibility = "inbound", "public"
            elif author_id is not None:
                direction, visibility = "outbound", "public"
            else:
                # Public comment with an unverifiable author: never treat it as
                # customer-facing when the requester is unknown.
                direction, visibility = "internal", "internal"

            comment_body = comment.get("body") or ""
            sent_at = _as_datetime_or_none(comment.get("created_at"))
            candidates.append(
                ConversationMessage(
                    organization_id=organization_id,
                    conversation_id=conversation.id,
                    provider=ZENDESK_PROVIDER,
                    external_message_id=external_message_id,
                    dedupe_key=f"zendesk_comment:{zendesk_ticket_id}:{external_message_id}",
                    direction=direction,
                    visibility=visibility,
                    body=comment_body,
                    sent_at=sent_at,
                )
            )
            if latest_sent_at is None or (sent_at is not None and sent_at > latest_sent_at):
                latest_sent_at = sent_at

        await cls._insert_messages_deduplicated(
            db,
            conversation_id=conversation.id,
            organization_id=organization_id,
            candidates=candidates,
        )

        await cls.sync_conversation_latest_message(
            db,
            conversation_id=conversation.id,
            organization_id=organization_id,
            sent_at=latest_sent_at,
        )

        # Record first response from provider-synced public outbound comments.
        await cls._record_first_response_from_candidates(
            db,
            ticket=local_ticket,
            candidates=candidates,
        )

        return conversation, comments_response

    @classmethod
    async def _record_first_response_from_candidates(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        candidates: list[ConversationMessage],
    ) -> None:
        """Update first_response_at from newly ingested public outbound comments."""
        outbound_public_sent_at = [
            m.sent_at
            for m in candidates
            if m.direction == "outbound"
            and m.visibility == "public"
            and m.sent_at is not None
        ]
        if not outbound_public_sent_at:
            return

        earliest = min(outbound_public_sent_at)
        await TicketSLAService.record_first_response(
            db,
            ticket=ticket,
            responded_at=earliest,
        )

    @classmethod
    async def _insert_messages_deduplicated(
        cls,
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
        candidates: list[ConversationMessage],
    ) -> None:
        """Insert provider-backed messages once, self-healing on collision."""
        external_ids = [
            m.external_message_id for m in candidates if m.external_message_id is not None
        ]
        existing = await ConversationMessageRepository.list_by_external_message_ids_for_tenant(
            db,
            organization_id=organization_id,
            provider=ZENDESK_PROVIDER,
            external_message_ids=external_ids,
        )

        missing = [
            m for m in candidates if m.external_message_id not in existing
        ]

        if not missing:
            return

        try:
            await ConversationMessageRepository.create_many(db, missing)
        except IntegrityError:
            await db.rollback()
            retry_ids = [
                m.external_message_id
                for m in missing
                if m.external_message_id is not None
            ]
            existing = (
                await ConversationMessageRepository.list_by_external_message_ids_for_tenant(
                    db,
                    organization_id=organization_id,
                    provider=ZENDESK_PROVIDER,
                    external_message_ids=retry_ids,
                )
            )
            still_missing = [
                m
                for m in missing
                if m.external_message_id not in existing
            ]
            for message in still_missing:
                await ConversationMessageRepository.create(db, message)

    @classmethod
    async def ensure_agent_reply_message(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        run_id: str,
        organization_id: int,
        dedupe_suffix: str,
        direction: str,
        visibility: str,
        body: str,
        external_message_id: str | None = None,
    ) -> ConversationMessage | None:
        """Mirror an agent-executed reply/note into the conversation.

        Idempotent by the deterministic agent-run key, so a re-executed run
        (recovery path) converges to a single mirrored message and can never
        cause a duplicate external reply. Returns ``None`` when the ticket has
        no conversation yet (legacy tickets get synced on their next sync).
        """
        conversation = await ConversationRepository.get_by_ticket_for_tenant(
            db,
            ticket_id=ticket.id,
            organization_id=organization_id,
        )
        if conversation is None:
            return None

        dedupe_key = f"agent_run:{run_id}:{dedupe_suffix}"

        existing = await ConversationMessageRepository.get_by_dedupe_key(
            db,
            conversation_id=conversation.id,
            organization_id=organization_id,
            dedupe_key=dedupe_key,
        )
        if existing is not None:
            return existing

        message = ConversationMessage(
            organization_id=organization_id,
            conversation_id=conversation.id,
            provider=ZENDESK_PROVIDER,
            external_message_id=external_message_id,
            dedupe_key=dedupe_key,
            direction=direction,
            visibility=visibility,
            body=body,
            sent_at=None,
        )

        try:
            message = await ConversationMessageRepository.create(db, message)
        except IntegrityError:
            await db.rollback()
            existing = await ConversationMessageRepository.get_by_dedupe_key(
                db,
                conversation_id=conversation.id,
                organization_id=organization_id,
                dedupe_key=dedupe_key,
            )
            if existing is not None:
                return existing
            raise

        await cls.sync_conversation_latest_message(
            db,
            conversation_id=conversation.id,
            organization_id=organization_id,
            sent_at=message.created_at,
        )

        return message