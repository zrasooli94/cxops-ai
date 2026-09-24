from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_request_id
from app.models.integration_job import IntegrationJob
from app.models.ticket import Ticket
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)
from app.repositories.ai_evaluation_repository import (
    AIEvaluationRepository,
)
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.repositories.ticket_event_repository import (
    TicketEventRepository,
)
from app.repositories.ticket_repository import TicketRepository
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
from app.services.service_escalation_service import (
    ServiceEscalationService,
)
from app.services.sla_escalation_scanner_service import (
    SLA_ESCALATION_JOB_TYPE,
)
from app.services.ticket_sla_service import TicketSLAService
from app.services.zendesk_webhook_service import (
    ZendeskWebhookService,
)


class AgentExecutionQueueBlockedError(Exception):
    pass


class IntegrationJobService:
    ZENDESK_TICKET_EVENT = "zendesk.ticket_event"
    AGENT_EXECUTION = "agent.execute"
    CONVERSATION_REPLY = JOB_TYPE_CONVERSATION_REPLY
    SLA_ESCALATION = SLA_ESCALATION_JOB_TYPE
    AI_EVALUATION = "ai.evaluation"

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
            raise AgentExecutionQueueBlockedError(f"Agent run {run_id} was not found.")

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
                raise ValueError("Zendesk webhook job is missing an organization binding")

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
                raise ValueError("Agent execution job is missing an organization binding")

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
                raise ValueError("Conversation reply job is missing an organization binding")

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

        # SLA escalation transition job
        if job.job_type == IntegrationJobService.SLA_ESCALATION:
            payload = job.payload
            organization_id = job.organization_id
            if organization_id is None:
                raise ValueError("SLA escalation job is missing an organization binding")

            ticket_id = int(payload["ticket_id"])
            milestone = str(payload["milestone"])
            expected_stage = str(payload.get("stage"))
            expected_cycle = int(payload.get("sla_cycle", 0))
            due_at_iso = payload.get("due_at")
            expected_due_at = None
            if due_at_iso:
                from datetime import datetime as _dt

                expected_due_at = _dt.fromisoformat(due_at_iso)

            ticket = await TicketRepository.get_by_id_for_tenant(
                db,
                ticket_id=ticket_id,
                organization_id=organization_id,
            )
            if ticket is None:
                return

            # Revalidation: stale jobs are no-ops.
            current_cycle = ticket.resolution_sla_cycle if milestone == "resolution" else 0
            current_due_at = (
                ticket.first_response_due_at
                if milestone == "first_response"
                else ticket.resolution_due_at
            )
            if current_cycle != expected_cycle or current_due_at != expected_due_at:
                return

            # A milestone that completed after this job was enqueued is stale:
            # completion re-evaluated the escalation via ensure_transition, which
            # resolves any lingering active escalation with milestone_completed.
            if (milestone == "first_response" and ticket.first_response_at is not None) or (
                milestone == "resolution" and ticket.resolved_at is not None
            ):
                return

            current_state = (
                TicketSLAService.first_response_state(ticket, now=datetime.now(UTC))
                if milestone == "first_response"
                else TicketSLAService.resolution_state(ticket, now=datetime.now(UTC))
            )
            expected_state_map = {
                "due_soon": "due_soon",
                "breached": "breached",
            }
            if current_state != expected_state_map.get(expected_stage):
                return

            escalation = await ServiceEscalationService.ensure_transition(
                db,
                ticket=ticket,
                milestone=milestone,  # type: ignore[arg-type]
                now=datetime.now(UTC),
            )

            if escalation is not None:
                from app.services.automation_service import AutomationService

                event_key = ServiceEscalationService.ticket_event_key(
                    escalation_id=escalation.id,
                    transition_version=escalation.transition_version,
                )
                event = await TicketEventRepository.get_by_event_key(db, event_key)
                if event is not None and not event.processed:
                    await AutomationService.process_ticket_event(
                        db,
                        event=event,
                        organization_id=organization_id,
                    )

            return

        # Durable AI evaluation job
        if job.job_type == IntegrationJobService.AI_EVALUATION:
            organization_id = job.organization_id

            if organization_id is None:
                raise ValueError("AI evaluation job is missing an organization binding")

            payload = job.payload
            evaluation_run_id = str(payload["evaluation_run_id"])
            target_type = str(payload["target_type"])

            # Imported lazily: ``agent_workflow_service`` imports this module at
            # import time, and AIEvaluationService pulls agent_evaluation_service
            # → agent_workflow_service, which would deadlock at module load.
            from app.services.ai_evaluation_service import AIEvaluationService

            try:
                if target_type == "rag":
                    await AIEvaluationService.execute_existing_rag_run(
                        db=db,
                        organization_id=organization_id,
                        run_id=evaluation_run_id,
                    )
                elif target_type == "agent":
                    await AIEvaluationService.execute_existing_agent_run(
                        db=db,
                        organization_id=organization_id,
                        run_id=evaluation_run_id,
                    )
                else:
                    raise ValueError(f"Unsupported AI evaluation target_type: {target_type}")
            except Exception as exc:
                # Job-facing errors stay type-name-only so the worker's
                # ``last_error``/logs never echo case input or model output;
                # the run itself already carries its own safe, specific error.
                raise RuntimeError(
                    f"ai.evaluation run {evaluation_run_id} failed: {type(exc).__name__}"
                ) from exc

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

    @staticmethod
    async def enqueue_ai_evaluation(
        db: AsyncSession,
        *,
        evaluation_run_id: str,
        target_type: str,
        organization_id: int,
    ) -> dict:
        """Enqueue a durable AI evaluation job for a provisioned run.

        Validates that the run exists for the tenant and that its ``target_type``
        matches before creating the job; the job payload never carries case input
        or customer content — the run's sanitized snapshot in ``run.input`` is
        the execution-time reference. Deduplication is per
        ``(organization_id, evaluation_run_id)``.
        """
        if target_type not in ("rag", "agent"):
            raise ValueError(f"Unsupported AI evaluation target_type: {target_type}")

        run = await AIEvaluationRepository.get_run_for_tenant(
            db=db,
            run_id=evaluation_run_id,
            organization_id=organization_id,
        )
        if run is None:
            raise ValueError(
                f"AI evaluation run {evaluation_run_id} was not found for the organization."
            )
        if run.target_type != target_type:
            raise ValueError(
                f"AI evaluation run {evaluation_run_id} is target_type "
                f"'{run.target_type}', not '{target_type}'."
            )

        dedupe_key = f"ai-evaluation:{organization_id}:{evaluation_run_id}"

        existing = await IntegrationJobRepository.get_by_dedupe_key(
            db,
            dedupe_key,
        )

        if existing:
            return {
                "evaluation_run_id": evaluation_run_id,
                "target_type": target_type,
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
                    job_type=(IntegrationJobService.AI_EVALUATION),
                    organization_id=organization_id,
                    payload={
                        "evaluation_run_id": evaluation_run_id,
                        "target_type": target_type,
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
                "evaluation_run_id": evaluation_run_id,
                "target_type": target_type,
                "job_id": existing.id,
                "status": existing.status,
                "duplicate": True,
            }

        return {
            "evaluation_run_id": evaluation_run_id,
            "target_type": target_type,
            "job_id": job.id,
            "status": job.status,
            "duplicate": False,
        }
