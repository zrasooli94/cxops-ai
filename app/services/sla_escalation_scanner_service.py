from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.integration_job import IntegrationJob
from app.models.ticket import Ticket
from app.repositories.integration_job_repository import IntegrationJobRepository
from app.repositories.ticket_repository import TicketRepository
from app.services.ticket_sla_service import (
    DUE_SOON_MINUTES,
    TicketSLAService,
)

SCAN_BATCH_SIZE = 200
DUE_SOON_WINDOW = timedelta(minutes=DUE_SOON_MINUTES)
SLA_ESCALATION_JOB_TYPE = "service.sla_escalation"

log = get_logger(__name__)


class SLAEscalationScannerService:
    """INTERNAL-ONLY background scanner for SLA escalation candidates.

    No human route may call this. Candidates are discovered across tenants;
    durable jobs are enqueued so every mutation re-fetches the ticket by
    (id, organization_id) and revalidates state before persisting anything.
    """

    @classmethod
    async def scan_once(
        cls,
        db: AsyncSession,
        *,
        now: datetime,
        batch_size: int = SCAN_BATCH_SIZE,
    ) -> tuple[int, int]:
        """Run one bounded scan cycle. Returns (tickets_evaluated, jobs_enqueued)."""
        evaluated = 0
        enqueued = 0
        offset = 0

        while True:
            candidates = await cls._list_candidates_unscoped(
                db,
                now=now,
                limit=batch_size,
                offset=offset,
            )
            if not candidates:
                break

            evaluated += len(candidates)

            for ticket_id, organization_id in candidates:
                enqueued += await cls._enqueue_milestone_jobs(
                    db,
                    ticket_id=ticket_id,
                    organization_id=organization_id,
                    now=now,
                )

            if len(candidates) < batch_size:
                break
            offset += batch_size

        return evaluated, enqueued

    @classmethod
    async def _list_candidates_unscoped(
        cls,
        db: AsyncSession,
        *,
        now: datetime,
        limit: int,
        offset: int,
    ) -> list[tuple[int, int]]:
        """INTERNAL-ONLY unscoped candidate discovery.

        Returns (ticket_id, organization_id) tuples. Worker-only.
        """
        due_soon_threshold = now + DUE_SOON_WINDOW

        first_response_predicate = (
            Ticket.first_response_at.is_(None)
            & Ticket.first_response_due_at.isnot(None)
            & (Ticket.first_response_due_at <= due_soon_threshold)
        )

        resolution_predicate = (
            Ticket.resolved_at.is_(None)
            & Ticket.resolution_due_at.isnot(None)
            & (Ticket.resolution_due_at <= due_soon_threshold)
        )

        stmt = (
            select(Ticket.id, Ticket.organization_id)
            .where(
                Ticket.organization_id.isnot(None),
                first_response_predicate | resolution_predicate,
            )
            .order_by(
                Ticket.first_response_due_at.asc().nulls_last(),
                Ticket.resolution_due_at.asc().nulls_last(),
                Ticket.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(stmt)
        return [(row.id, row.organization_id) for row in result.all()]

    @classmethod
    async def _enqueue_milestone_jobs(
        cls,
        db: AsyncSession,
        *,
        ticket_id: int,
        organization_id: int,
        now: datetime,
    ) -> int:
        """Enqueue durable jobs for milestones that warrant escalation.

        Returns the number of jobs enqueued.
        """
        ticket = await TicketRepository.get_by_id_for_tenant(
            db,
            ticket_id=ticket_id,
            organization_id=organization_id,
        )
        if ticket is None:
            return 0

        enqueued = 0
        for milestone in ("first_response", "resolution"):
            stage = cls._current_stage(ticket, milestone=milestone, now=now)
            if stage is None:
                continue

            due_at = (
                ticket.first_response_due_at
                if milestone == "first_response"
                else ticket.resolution_due_at
            )
            cycle = ticket.resolution_sla_cycle if milestone == "resolution" else 0

            dedupe_key = cls._job_dedupe_key(
                organization_id=organization_id,
                ticket_id=ticket_id,
                milestone=milestone,
                stage=stage,
                sla_cycle=cycle,
                due_at=due_at,
            )

            existing = await IntegrationJobRepository.get_by_dedupe_key(db, dedupe_key)
            if existing is not None:
                continue

            job = IntegrationJob(
                organization_id=organization_id,
                dedupe_key=dedupe_key,
                job_type=SLA_ESCALATION_JOB_TYPE,
                payload={
                    "ticket_id": ticket_id,
                    "milestone": milestone,
                    "stage": stage,
                    "sla_cycle": cycle,
                    "due_at": due_at.isoformat() if due_at else None,
                },
            )
            IntegrationJobRepository.add(db, job)
            try:
                await IntegrationJobRepository.flush(db)
                enqueued += 1
            except IntegrityError:
                await db.rollback()
                log.debug(
                    "sla_job_dedupe_race",
                    dedupe_key=dedupe_key,
                )

        if enqueued > 0:
            await db.commit()

        return enqueued

    @classmethod
    def _current_stage(
        cls,
        ticket: Ticket,
        *,
        milestone: str,
        now: datetime,
    ) -> str | None:
        if milestone == "first_response":
            state = TicketSLAService.first_response_state(ticket, now=now)
        else:
            state = TicketSLAService.resolution_state(ticket, now=now)

        if state in ("on_track", "not_configured", "met"):
            return None
        if state == "due_soon":
            return "due_soon"
        return "breached"

    @classmethod
    def _job_dedupe_key(
        cls,
        *,
        organization_id: int,
        ticket_id: int,
        milestone: str,
        stage: str,
        sla_cycle: int,
        due_at: datetime | None,
    ) -> str:
        deadline_epoch = int(due_at.timestamp()) if due_at is not None else 0
        return (
            f"sla-escalation:{organization_id}:{ticket_id}:"
            f"{milestone}:{stage}:{sla_cycle}:{deadline_epoch}"
        )
