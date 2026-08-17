from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_job import IntegrationJob
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.schemas.job import JobAccepted
from app.services.zendesk_webhook_service import (
    ZendeskWebhookService,
)


class IntegrationJobService:

    ZENDESK_TICKET_EVENT = "zendesk.ticket_event"

    @staticmethod
    async def enqueue_zendesk_event(
        db: AsyncSession,
        *,
        invocation_id: str,
        event_type: str,
        zendesk_ticket_id: int,
        payload: dict,
    ) -> JobAccepted:

        existing = (
            await IntegrationJobRepository.get_by_dedupe_key(
                db=db,
                dedupe_key=invocation_id,
            )
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
            job_type=(
                IntegrationJobService.ZENDESK_TICKET_EVENT
            ),
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

            existing = (
                await IntegrationJobRepository.get_by_dedupe_key(
                    db=db,
                    dedupe_key=invocation_id,
                )
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

        if (
            job.job_type
            == IntegrationJobService.ZENDESK_TICKET_EVENT
        ):
            payload = job.payload

            await ZendeskWebhookService.process(
                db=db,
                invocation_id=payload["invocation_id"],
                event_type=payload["event_type"],
                zendesk_ticket_id=payload[
                    "zendesk_ticket_id"
                ],
                payload=payload["payload"],
            )

            return

        raise ValueError(
            f"Unknown job type: {job.job_type}"
        )