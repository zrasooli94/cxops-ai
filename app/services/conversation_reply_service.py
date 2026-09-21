"""Human reply submission and durable enqueue.

A human reply is a ``ConversationMessage`` plus a durable ``IntegrationJob``
created atomically in one transaction. The message owns the body and delivery
lifecycle; the job owns the asynchronous delivery attempt. This module never
performs external network calls — delivery is the worker's responsibility via
``ConversationDeliveryService``.
"""

from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rbac import AuthorizationContext, Capability, require_capability
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.integration_job_repository import IntegrationJobRepository

log = get_logger("conversation_reply")

MAX_REPLY_BODY_CHARS = 10000

DELIVERY_STATUS_QUEUED = "queued"
DELIVERY_STATUS_SENDING = "sending"
DELIVERY_STATUS_RETRYING = "retrying"
DELIVERY_STATUS_SENT = "sent"
DELIVERY_STATUS_FAILED = "failed"

JOB_TYPE_CONVERSATION_REPLY = "conversation.reply"

# Safe bounded failure categories exposed to clients.
ERROR_REPLY_UNAVAILABLE = "reply_unavailable"
ERROR_CONVERSATION_CLOSED = "conversation_closed"
ERROR_PROVIDER_UNSUPPORTED = "provider_unsupported"
ERROR_MESSAGE_NOT_RETRYABLE = "message_not_retryable"


class ConversationReplyError(Exception):
    """User-facing safe reply error."""

    def __init__(self, error_code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class ConversationReplyService:
    """Create and manage human-submitted outbound conversation replies."""

    @staticmethod
    def _validate_body(body: str) -> str:
        if body is None:
            raise ConversationReplyError(
                ERROR_REPLY_UNAVAILABLE, "Reply body is required", 400
            )
        cleaned = str(body).strip()
        if not cleaned:
            raise ConversationReplyError(
                ERROR_REPLY_UNAVAILABLE, "Reply body cannot be empty", 400
            )
        if len(cleaned) > MAX_REPLY_BODY_CHARS:
            raise ConversationReplyError(
                ERROR_REPLY_UNAVAILABLE,
                f"Reply body exceeds {MAX_REPLY_BODY_CHARS} characters",
                400,
            )
        return cleaned

    @staticmethod
    def _reply_eligibility(conversation: Conversation) -> None:
        """Fail closed if the conversation cannot accept a human reply."""
        if conversation.status != "open":
            raise ConversationReplyError(
                ERROR_CONVERSATION_CLOSED,
                "Conversation is closed",
                409,
            )

        if conversation.provider not in {"zendesk", "cxops"}:
            raise ConversationReplyError(
                ERROR_PROVIDER_UNSUPPORTED,
                f"Provider '{conversation.provider}' does not support human replies",
                422,
            )

    @classmethod
    async def enqueue_reply(
        cls,
        db: AsyncSession,
        *,
        conversation_id: int,
        body: str,
        client_request_id: str,
        organization_id: int,
        requested_by_subject: str,
        authz: AuthorizationContext | None = None,
    ) -> dict:
        """Atomically create a human reply message and its durable delivery job.

        Returns the existing accepted reply if the same client_request_id is
        replayed. Duplicate detection is by ``conversation_id + dedupe_key``,
        not by body text.
        """
        if authz is not None:
            require_capability(authz, Capability.TICKET_WRITE)
            requested_by_subject = authz.subject

        cleaned_body = cls._validate_body(body)

        if not client_request_id:
            raise ConversationReplyError(
                ERROR_REPLY_UNAVAILABLE,
                "client_request_id is required",
                400,
            )

        conversation = await ConversationRepository.get_by_id_for_tenant(
            db,
            conversation_id=conversation_id,
            organization_id=organization_id,
        )
        if conversation is None:
            raise ConversationReplyError(
                ERROR_REPLY_UNAVAILABLE,
                "Conversation not found",
                404,
            )

        cls._reply_eligibility(conversation)

        dedupe_key = f"human_reply:{client_request_id}"
        delivery_token = str(uuid4())
        job_dedupe_key = f"conversation-reply:{organization_id}:{conversation_id}:{dedupe_key}"

        # Idempotency: return an existing message+job created from the same
        # client request id without creating duplicates.
        existing_message = (
            await ConversationMessageRepository.get_by_dedupe_key(
                db,
                conversation_id=conversation.id,
                organization_id=organization_id,
                dedupe_key=dedupe_key,
            )
        )
        if existing_message is not None:
            existing_job = await IntegrationJobRepository.get_by_dedupe_key(
                db,
                dedupe_key=job_dedupe_key,
            )
            return {
                "message_id": existing_message.id,
                "delivery_status": existing_message.delivery_status,
                "duplicate": True,
                "job_id": existing_job.id if existing_job is not None else None,
            }

        message = ConversationMessage(
            organization_id=organization_id,
            conversation_id=conversation.id,
            provider=conversation.provider,
            dedupe_key=dedupe_key,
            external_message_id=None,
            direction="outbound",
            visibility="public",
            body=cleaned_body,
            sent_at=None,
            delivery_status=DELIVERY_STATUS_QUEUED,
            delivery_token=delivery_token,
            requested_by_subject=requested_by_subject,
            delivery_error_code=None,
            delivered_at=None,
        )

        ConversationMessageRepository.add(db, message)
        await ConversationMessageRepository.flush(db)

        job = IntegrationJob(
            organization_id=organization_id,
            dedupe_key=job_dedupe_key,
            job_type=JOB_TYPE_CONVERSATION_REPLY,
            payload={
                "message_id": message.id,
                "request_id": client_request_id,
            },
        )

        IntegrationJobRepository.add(db, job)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()

            # Race lost: another request created the message/job first.
            # Re-resolve and return the winner without surfacing PII.
            existing_message = (
                await ConversationMessageRepository.get_by_dedupe_key(
                    db,
                    conversation_id=conversation.id,
                    organization_id=organization_id,
                    dedupe_key=dedupe_key,
                )
            )
            if existing_message is None:
                raise

            existing_job = await IntegrationJobRepository.get_by_dedupe_key(
                db,
                dedupe_key=job_dedupe_key,
            )
            return {
                "message_id": existing_message.id,
                "delivery_status": existing_message.delivery_status,
                "duplicate": True,
                "job_id": existing_job.id if existing_job is not None else None,
            }

        await db.refresh(message)
        await db.refresh(job)

        log.info(
            "human_reply_queued",
            organization_id=organization_id,
            conversation_id=conversation.id,
            message_id=message.id,
            job_id=job.id,
        )

        return {
            "message_id": message.id,
            "delivery_status": message.delivery_status,
            "duplicate": False,
            "job_id": job.id,
        }

    @classmethod
    async def retry_failed_reply(
        cls,
        db: AsyncSession,
        *,
        conversation_id: int,
        message_id: int,
        organization_id: int,
        authz: AuthorizationContext | None = None,
    ) -> dict:
        """Requeue a permanently failed human reply for delivery."""
        if authz is not None:
            require_capability(authz, Capability.TICKET_WRITE)

        message = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
            db,
            message_id=message_id,
            conversation_id=conversation_id,
            organization_id=organization_id,
        )
        if message is None:
            raise ConversationReplyError(
                ERROR_REPLY_UNAVAILABLE,
                "Message not found",
                404,
            )

        if message.delivery_status != DELIVERY_STATUS_FAILED:
            raise ConversationReplyError(
                ERROR_MESSAGE_NOT_RETRYABLE,
                "Only failed deliveries can be retried",
                409,
            )

        conversation = await ConversationRepository.get_by_id_for_tenant(
            db,
            conversation_id=message.conversation_id,
            organization_id=organization_id,
        )
        if conversation is None:
            raise ConversationReplyError(
                ERROR_REPLY_UNAVAILABLE,
                "Conversation not found",
                404,
            )

        cls._reply_eligibility(conversation)

        job_dedupe_key = f"conversation-reply:{organization_id}:{conversation.id}:{message.dedupe_key}"
        job = await IntegrationJobRepository.requeue_failed_for_tenant(
            db,
            dedupe_key=job_dedupe_key,
            organization_id=organization_id,
        )
        if job is None:
            raise ConversationReplyError(
                ERROR_MESSAGE_NOT_RETRYABLE,
                "No failed delivery job found for this message",
                409,
            )

        await ConversationMessageRepository.update_for_tenant(
            db,
            message=message,
            changes={
                "delivery_status": DELIVERY_STATUS_RETRYING,
                "delivery_error_code": None,
            },
            organization_id=organization_id,
        )
        await db.commit()

        log.info(
            "human_reply_retry_manual",
            organization_id=organization_id,
            conversation_id=conversation.id,
            message_id=message.id,
            job_id=job.id,
        )

        return {
            "message_id": message.id,
            "delivery_status": message.delivery_status,
            "duplicate": False,
            "job_id": job.id,
        }

    @classmethod
    async def handle_job_failure(
        cls,
        db: AsyncSession,
        *,
        message_id: int,
        organization_id: int,
        error_code: str | None,
        will_retry: bool,
    ) -> None:
        """Update message delivery_status after a job failure.

        Called by the integration job service when a delivery job fails. This
        is intentionally non-raising: the job's own status already reflects the
        retry/failed decision.
        """
        message = await ConversationMessageRepository.get_by_id_for_tenant(
            db,
            message_id=message_id,
            organization_id=organization_id,
        )
        if message is None:
            return

        if message.delivery_status == DELIVERY_STATUS_SENT:
            return

        changes: dict = {
            "delivery_error_code": error_code,
        }
        if will_retry:
            changes["delivery_status"] = DELIVERY_STATUS_RETRYING
        else:
            changes["delivery_status"] = DELIVERY_STATUS_FAILED

        await ConversationMessageRepository.update_for_tenant(
            db,
            message=message,
            changes=changes,
            organization_id=organization_id,
        )
        await db.commit()
