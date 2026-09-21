from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_request_id
from app.models.integration_job import IntegrationJob
from app.models.ticket import Ticket
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.schemas.job import JobAccepted
from app.services.agent_execution_service import (
    agent_execution_service,
)
from app.services.conversation_delivery_service import (
    DELIVERY_ERROR_PROVIDER_UNAVAILABLE,
    ConversationDeliveryError,
    ConversationDeliveryService,
)
from app.services.conversation_reply_service import (
    JOB_TYPE_CONVERSATION_REPLY,
    ConversationReplyService,
)
from app.services.zendesk_webhook_service import (
    ZendeskWebhookService,
)


class AgentExecutionQueueBlockedError(Exception):
    pass


class IntegrationJobService:
    ZENDESK_TICKET_EVENT = "zendesk.ticket_event"
    AGENT_EXECUTION = "agent.execute"
    CONVERSATION_REPLY = JOB_TYPE_CONVERSATION_REPLY

    @staticmethod
    async def _assert_agent_execution_target(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
    ) -> None:
        run = await AgentRunRepository.get_by_run_id_for_tenant(
            db=db,
            run_id=run_id,
            organization_id=organization_id,
        )

        if run is None:
            raise AgentExecutionQueueBlockedError(
                f"Agent run {run_id} was not found."
            )

        result = await db.execute(
            select(Ticket).where(
                Ticket.id == run.ticket_id,
                Ticket.organization_id == organization_id,
            )
        )

        ticket = result.scalar_one_or_none()

        if ticket is None:
            raise AgentExecutionQueueBlockedError(
                "The local ticket for this agent run was not found."
            )

        if not ticket.external_id:
            raise AgentExecutionQueueBlockedError(
                "External execution is disabled for "
                "local demo tickets that are not linked "
                "to Zendesk."
            )

        try:
            int(ticket.external_id)

        except (TypeError, ValueError) as exc:
            raise AgentExecutionQueueBlockedError(
                "The ticket has an invalid Zendesk external_id."
            ) from exc

    @staticmethod
    async def enqueue_zendesk_event(
        db: AsyncSession,
        *,
        invocation_id: str,
        event_type: str,
        zendesk_ticket_id: int,
        payload: dict,
        organization_id: int,
    ) -> JobAccepted:
        existing = await IntegrationJobRepository.get_by_dedupe_key(
            db=db,
            dedupe_key=invocation_id,
        )

        if existing:
            return JobAccepted(
                event_id=invocation_id,
                job_id=existing.id,
                duplicate=True,
                status=existing.status,
            )

        job = IntegrationJob(
            organization_id=organization_id,
            dedupe_key=invocation_id,
            job_type=(IntegrationJobService.ZENDESK_TICKET_EVENT),
            payload={
                "invocation_id": invocation_id,
                "event_type": event_type,
                "zendesk_ticket_id": (zendesk_ticket_id),
                "payload": payload,
            },
        )

        try:
            job = await IntegrationJobRepository.create(
                db=db,
                job=job,
            )

        except IntegrityError:
            await db.rollback()

            existing = await IntegrationJobRepository.get_by_dedupe_key(
                db=db,
                dedupe_key=invocation_id,
            )

            if existing is None:
                raise

            return JobAccepted(
                event_id=invocation_id,
                job_id=existing.id,
                duplicate=True,
                status=existing.status,
            )

        return JobAccepted(
            event_id=invocation_id,
            job_id=job.id,
            duplicate=False,
            status="queued",
        )

    @staticmethod
    async def execute(
        db: AsyncSession,
        job: IntegrationJob,
    ) -> None:
        # Zendesk webhook event
        if job.job_type == IntegrationJobService.ZENDESK_TICKET_EVENT:
            payload = job.payload

            if job.organization_id is None:
                raise ValueError(
                    "Zendesk webhook job is missing an organization binding"
                )

            await ZendeskWebhookService.process(
                db=db,
                invocation_id=(payload["invocation_id"]),
                event_type=(payload["event_type"]),
                zendesk_ticket_id=(payload["zendesk_ticket_id"]),
                payload=payload["payload"],
                organization_id=job.organization_id,
            )

            return

        # Approved agent execution
        if job.job_type == IntegrationJobService.AGENT_EXECUTION:
            payload = job.payload

            # Agent-execution jobs bind the run's organization at enqueue time
            # (see ``enqueue_agent_execution``). Re-deriving it here — rather
            # than trusting the payload — keeps the execution tenant-bound even
            # under a crafted or stale payload.
            organization_id = job.organization_id

            if organization_id is None:
                raise ValueError(
                    "Agent execution job is missing an organization binding"
                )

            await agent_execution_service.execute(
                db=db,
                run_id=str(payload["run_id"]),
                organization_id=organization_id,
            )

            return

        # Human reply delivery
        if job.job_type == IntegrationJobService.CONVERSATION_REPLY:
            organization_id = job.organization_id

            if organization_id is None:
                raise ValueError(
                    "Conversation reply job is missing an organization binding"
                )

            payload = job.payload
            message_id = int(payload["message_id"])

            message = await ConversationMessageRepository.get_by_id_for_tenant(
                db,
                message_id=message_id,
                organization_id=organization_id,
            )
            if message is None:
                raise ValueError("Conversation reply job references a missing message")

            await ConversationMessageRepository.update_for_tenant(
                db,
                message=message,
                changes={"delivery_status": "sending"},
                organization_id=organization_id,
            )

            try:
                await ConversationDeliveryService().deliver(
                    db=db,
                    message_id=message_id,
                    organization_id=organization_id,
                )

            except ConversationDeliveryError as exc:
                # Non-retryable delivery failure (configuration, unsupported
                # provider, invalid target). Mark the job and message failed
                # now rather than exhausting retries.
                await db.rollback()

                job.status = "failed"
                job.locked_at = None
                job.last_error = str(exc)[:4000]

                await ConversationMessageRepository.update_for_tenant(
                    db,
                    message=message,
                    changes={
                        "delivery_status": "failed",
                        "delivery_error_code": exc.error_code,
                    },
                    organization_id=organization_id,
                )

                await db.commit()
                return

            return

        raise ValueError(f"Unknown job type: {job.job_type}")

    @staticmethod
    async def handle_failure(
        db: AsyncSession,
        *,
        job: IntegrationJob,
        error_message: str,
    ) -> None:
        """Job-type-specific failure cleanup after the generic retry decision.

        Called by the worker after ``mark_failed`` has decided retry vs failed.
        For human replies, this synchronizes the message delivery_status with
        the job's final retry/failed state.
        """
        if job.job_type != IntegrationJobService.CONVERSATION_REPLY:
            return

        organization_id = job.organization_id
        if organization_id is None:
            return

        payload = job.payload
        if not isinstance(payload, dict):
            return

        message_id = payload.get("message_id")
        if message_id is None:
            return

        will_retry = job.status == "retry"
        await ConversationReplyService.handle_job_failure(
            db=db,
            message_id=int(message_id),
            organization_id=organization_id,
            error_code=DELIVERY_ERROR_PROVIDER_UNAVAILABLE,
            will_retry=will_retry,
        )

    @staticmethod
    async def enqueue_agent_execution(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
    ) -> dict:
        # Validate the external execution target
        # before creating a durable queue job.
        #
        # Local demo tickets may be analyzed and
        # reviewed, but they cannot produce
        # external Zendesk writes.

        await IntegrationJobService._assert_agent_execution_target(
            db=db,
            run_id=run_id,
            organization_id=organization_id,
        )

        dedupe_key = f"agent-execution:{run_id}"

        existing = await IntegrationJobRepository.get_by_dedupe_key(
            db,
            dedupe_key,
        )

        if existing:
            return {
                "run_id": run_id,
                "job_id": existing.id,
                "status": existing.status,
                "duplicate": True,
            }

        # Propagate request correlation ID if available
        request_id = get_request_id()

        try:
            job = await IntegrationJobRepository.create(
                db,
                job=IntegrationJob(
                    dedupe_key=dedupe_key,
                    job_type=(IntegrationJobService.AGENT_EXECUTION),
                    organization_id=organization_id,
                    payload={
                        "run_id": run_id,
                        "request_id": request_id,
                    },
                ),
            )

        except IntegrityError:
            await db.rollback()

            existing = await IntegrationJobRepository.get_by_dedupe_key(
                db,
                dedupe_key,
            )

            if existing is None:
                raise

            return {
                "run_id": run_id,
                "job_id": existing.id,
                "status": existing.status,
                "duplicate": True,
            }

        return {
            "run_id": run_id,
            "job_id": job.id,
            "status": job.status,
            "duplicate": False,
        }
