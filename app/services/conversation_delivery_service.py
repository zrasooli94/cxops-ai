"""Provider-neutral human reply delivery boundary.

The delivery service receives a durable ``ConversationMessage`` that has
already been queued by ``ConversationReplyService`` and routes it to the
provider-specific adapter. All tenant resolution and provider-target validation
happens here, inside the worker's trusted ``organization_id``.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import record_human_reply_delivery
from app.integrations.zendesk.client import ZendeskClient
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.ticket import Ticket
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import ConversationRepository
from app.services.zendesk_target_service import resolve_zendesk_execution_target

log = get_logger("conversation_delivery")

# Bounded safe failure categories. No body, PII, or provider response is stored.
DELIVERY_ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
DELIVERY_ERROR_TARGET_UNAVAILABLE = "target_unavailable"
DELIVERY_ERROR_PROVIDER_REJECTED = "provider_rejected"
DELIVERY_ERROR_CONFIGURATION_UNAVAILABLE = "configuration_unavailable"
DELIVERY_ERROR_UNSUPPORTED = "unsupported"

# Provider marker prefix used for Zendesk exactly-once delivery recovery.
# This text is intentionally customer-visible so humans can correlate replies.
REPLY_MARKER_PREFIX = "CXOps Reply Ref:"

MAX_ZENDESK_COMMENTS_FOR_MARKER = 50


class ConversationDeliveryError(Exception):
    """Non-retryable delivery failure (e.g. misconfiguration, unsupported)."""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class ConversationDeliveryTransientError(Exception):
    """Retryable delivery failure (e.g. provider unavailable)."""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


def _reply_marker(delivery_token: str) -> str:
    return f"{REPLY_MARKER_PREFIX} {delivery_token}"


class ZendeskConversationDeliveryAdapter:
    """Deliver human public replies to Zendesk with marker-based recovery."""

    def __init__(self) -> None:
        self.zendesk = ZendeskClient()

    async def deliver(
        self,
        db: AsyncSession,
        *,
        message: ConversationMessage,
        conversation: Conversation,
        ticket: Ticket,
        organization_id: int,
    ) -> None:
        """Deliver a human reply to Zendesk exactly once.

        The delivery is idempotent: before every send attempt we scan recent
        comments for the reply's unique marker. If the marker already exists,
        the external write is skipped and the local message is reconciled.
        """
        zendesk_ticket_id = resolve_zendesk_execution_target(ticket)
        if zendesk_ticket_id is None:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Ticket is not a valid Zendesk execution target",
            )

        if conversation.ticket_id != ticket.id:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Conversation is not linked to the resolved ticket",
            )

        if conversation.external_thread_id != ticket.external_id:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Conversation thread id does not match ticket external id",
            )

        delivery_token = message.delivery_token
        if delivery_token is None:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Message has no delivery token",
            )

        marker = _reply_marker(delivery_token)

        existing_comment_id = await self._find_marker_comment(
            db,
            zendesk_ticket_id=zendesk_ticket_id,
            marker=marker,
            organization_id=organization_id,
        )

        if existing_comment_id is not None:
            log.info(
                "zendesk_reply_recovered",
                organization_id=organization_id,
                conversation_id=conversation.id,
                message_id=message.id,
            )
            await self._mark_sent(
                db,
                message=message,
                conversation=conversation,
                organization_id=organization_id,
                external_message_id=existing_comment_id,
            )
            return

        reply_body = f"{message.body}\n\n{marker}"

        try:
            await self.zendesk.apply_agent_action(
                db,
                zendesk_ticket_id,
                organization_id=organization_id,
                comment=reply_body,
                public=True,
            )
        except Exception as exc:
            log.exception(
                "zendesk_reply_failed",
                organization_id=organization_id,
                conversation_id=conversation.id,
                message_id=message.id,
            )
            if isinstance(exc, ConversationDeliveryError):
                raise
            raise ConversationDeliveryTransientError(
                DELIVERY_ERROR_PROVIDER_UNAVAILABLE,
                "Zendesk reply request failed",
            ) from exc

        # After a successful write, resolve the provider comment id so later
        # Zendesk comment sync does not duplicate this message.
        external_message_id = await self._find_marker_comment(
            db,
            zendesk_ticket_id=zendesk_ticket_id,
            marker=marker,
            organization_id=organization_id,
        )

        await self._mark_sent(
            db,
            message=message,
            conversation=conversation,
            organization_id=organization_id,
            external_message_id=external_message_id,
        )

    async def _find_marker_comment(
        self,
        db: AsyncSession,
        *,
        zendesk_ticket_id: int,
        marker: str,
        organization_id: int,
    ) -> str | None:
        try:
            response = await self.zendesk.get_ticket_comments(
                db,
                ticket_id=zendesk_ticket_id,
                organization_id=organization_id,
            )
        except Exception:
            log.exception(
                "zendesk_marker_scan_failed",
                zendesk_ticket_id=zendesk_ticket_id,
                organization_id=organization_id,
            )
            return None

        comments = response.get("comments") or []
        for comment in comments[-MAX_ZENDESK_COMMENTS_FOR_MARKER:]:
            body = str(comment.get("body", ""))
            if marker in body:
                comment_id = comment.get("id")
                if comment_id is not None:
                    return str(comment_id)
        return None

    async def _mark_sent(
        self,
        db: AsyncSession,
        *,
        message: ConversationMessage,
        conversation: Conversation,
        organization_id: int,
        external_message_id: str | None,
    ) -> None:
        now = datetime.now(UTC)
        changes: dict = {
            "delivery_status": "sent",
            "delivered_at": now,
        }
        if external_message_id is not None:
            # If Zendesk comment sync already ingested this comment under another
            # local message, keep the human reply row without an external id to
            # avoid a unique-constraint collision. The provider-side comment is
            # still represented in the thread.
            existing = await ConversationMessageRepository.list_by_external_message_ids_for_tenant(
                db,
                organization_id=organization_id,
                provider="zendesk",
                external_message_ids=[external_message_id],
            )
            if external_message_id not in existing:
                changes["external_message_id"] = external_message_id

        await ConversationMessageRepository.update_for_tenant(
            db,
            message=message,
            changes=changes,
            organization_id=organization_id,
        )

        sent_at = message.sent_at or now
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


class LocalConversationDeliveryAdapter:
    """Local-only CXOps reply delivery: no external network call."""

    async def deliver(
        self,
        db: AsyncSession,
        *,
        message: ConversationMessage,
        conversation: Conversation,
        ticket: Ticket | None,
        organization_id: int,
    ) -> None:
        now = datetime.now(UTC)

        await ConversationMessageRepository.update_for_tenant(
            db,
            message=message,
            changes={
                "delivery_status": "sent",
                "sent_at": now,
                "delivered_at": now,
            },
            organization_id=organization_id,
        )

        if (
            conversation.latest_message_at is None
            or now > conversation.latest_message_at
        ):
            await ConversationRepository.update_for_tenant(
                db,
                conversation=conversation,
                changes={"latest_message_at": now},
                organization_id=organization_id,
            )


class ConversationDeliveryService:
    """Dispatch a queued human reply to the correct provider adapter."""

    def __init__(self) -> None:
        self._zendesk = ZendeskConversationDeliveryAdapter()
        self._local = LocalConversationDeliveryAdapter()

    async def deliver(
        self,
        db: AsyncSession,
        *,
        message_id: int,
        organization_id: int,
    ) -> None:
        """Deliver a queued/retrying human reply.

        Re-resolves all persisted tenant-owned relationships. Fails closed on
        tenant mismatch, unsupported provider, or invalid target.
        """
        message = await ConversationMessageRepository.get_by_id_for_tenant(
            db,
            message_id=message_id,
            organization_id=organization_id,
        )
        if message is None:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Message not found",
            )

        if message.delivery_token is None:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Message has no delivery token",
            )

        if message.delivery_status == "sent":
            # Already delivered; job completion is idempotent.
            return

        if message.delivery_status not in {"queued", "retrying", "sending"}:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Message is not deliverable",
            )

        conversation = await ConversationRepository.get_by_id_for_tenant(
            db,
            conversation_id=message.conversation_id,
            organization_id=organization_id,
        )
        if conversation is None:
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Conversation not found",
            )

        ticket = None
        if conversation.ticket_id is not None:
            from app.repositories.ticket_repository import TicketRepository

            ticket = await TicketRepository.get_by_id_for_tenant(
                db,
                ticket_id=conversation.ticket_id,
                organization_id=organization_id,
            )

        if conversation.status != "open":
            raise ConversationDeliveryError(
                DELIVERY_ERROR_TARGET_UNAVAILABLE,
                "Conversation is closed",
            )

        await ConversationMessageRepository.update_for_tenant(
            db,
            message=message,
            changes={"delivery_status": "sending"},
            organization_id=organization_id,
        )

        provider = conversation.provider

        if provider == "zendesk":
            if ticket is None:
                raise ConversationDeliveryError(
                    DELIVERY_ERROR_TARGET_UNAVAILABLE,
                    "Zendesk conversation has no linked ticket",
                )
            await self._zendesk.deliver(
                db,
                message=message,
                conversation=conversation,
                ticket=ticket,
                organization_id=organization_id,
            )
            record_human_reply_delivery(
                provider=provider,
                outcome="sent",
            )
            return

        if provider == "cxops":
            await self._local.deliver(
                db,
                message=message,
                conversation=conversation,
                ticket=ticket,
                organization_id=organization_id,
            )
            record_human_reply_delivery(
                provider=provider,
                outcome="sent",
            )
            return

        raise ConversationDeliveryError(
            DELIVERY_ERROR_UNSUPPORTED,
            f"Provider '{provider}' is not supported for human replies",
        )
