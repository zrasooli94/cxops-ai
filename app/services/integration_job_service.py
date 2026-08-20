from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_job import IntegrationJob
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.schemas.job import JobAccepted
from app.services.agent_execution_service import (
    agent_execution_service,
)
from app.services.zendesk_webhook_service import (
    ZendeskWebhookService,
)


class IntegrationJobService:
    ZENDESK_TICKET_EVENT = "zendesk.ticket_event"
    AGENT_EXECUTION = "agent.execute"

    @staticmethod
    async def enqueue_zendesk_event(
        db: AsyncSession,
        *,
        invocation_id: str,
        event_type: str,
        zendesk_ticket_id: int,
        payload: dict,
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
            dedupe_key=invocation_id,
            job_type=(IntegrationJobService.ZENDESK_TICKET_EVENT),
            payload={
                "invocation_id": invocation_id,
                "event_type": event_type,
                "zendesk_ticket_id": zendesk_ticket_id,
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

        # -----------------------------------------
        # Zendesk webhook event
        # -----------------------------------------

        if job.job_type == IntegrationJobService.ZENDESK_TICKET_EVENT:
            payload = job.payload

            await ZendeskWebhookService.process(
                db=db,
                invocation_id=payload["invocation_id"],
                event_type=payload["event_type"],
                zendesk_ticket_id=payload["zendesk_ticket_id"],
                payload=payload["payload"],
            )

            return

        # -----------------------------------------
        # Approved agent execution
        # -----------------------------------------

        if job.job_type == IntegrationJobService.AGENT_EXECUTION:
            payload = job.payload

            await agent_execution_service.execute(
                db=db,
                run_id=str(payload["run_id"]),
            )

            return

        # -----------------------------------------
        # Unsupported job
        # -----------------------------------------

        raise ValueError(f"Unknown job type: {job.job_type}")

    @staticmethod
    async def enqueue_agent_execution(
        db: AsyncSession,
        *,
        run_id: str,
    ) -> dict:

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

        try:
            job = await IntegrationJobRepository.create(
                db,
                job=IntegrationJob(
                    dedupe_key=dedupe_key,
                    job_type=(IntegrationJobService.AGENT_EXECUTION),
                    payload={
                        "run_id": run_id,
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
