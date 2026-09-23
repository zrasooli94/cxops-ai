from datetime import datetime
from typing import ClassVar, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import (
    record_escalation_acknowledged,
    record_escalation_transition,
)
from app.models.service_escalation import ServiceEscalation
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from app.repositories.service_escalation_repository import (
    ServiceEscalationRepository,
)
from app.repositories.ticket_event_repository import TicketEventRepository
from app.services.ticket_sla_service import TicketSLAService

EscalationMilestone = Literal["first_response", "resolution"]
EscalationStage = Literal["due_soon", "breached"]

log = get_logger(__name__)


class ServiceEscalationService:
    """Domain service for durable SLA escalation lifecycle."""

    SLA_EVENT_TYPES: ClassVar[dict[EscalationMilestone, dict[EscalationStage, str]]] = {
        "first_response": {
            "due_soon": "sla.first_response.due_soon",
            "breached": "sla.first_response.breached",
        },
        "resolution": {
            "due_soon": "sla.resolution.due_soon",
            "breached": "sla.resolution.breached",
        },
    }

    @classmethod
    def event_key_for_escalation(
        cls,
        *,
        organization_id: int,
        ticket_id: int,
        milestone: EscalationMilestone,
        stage: EscalationStage,
        resolution_sla_cycle: int,
        due_at: datetime | None,
    ) -> str:
        deadline_epoch = int(due_at.timestamp()) if due_at is not None else 0
        return (
            f"sla:{organization_id}:{ticket_id}:{milestone}:"
            f"{stage}:{resolution_sla_cycle}:{deadline_epoch}"
        )

    @classmethod
    def ticket_event_key(cls, *, escalation_id: int, transition_version: int) -> str:
        return f"sla-event:{escalation_id}:{transition_version}"

    @classmethod
    def _current_stage(
        cls,
        ticket: Ticket,
        *,
        milestone: EscalationMilestone,
        now: datetime,
    ) -> EscalationStage | None:
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
    async def _ensure_event(
        cls,
        db: AsyncSession,
        *,
        escalation: ServiceEscalation,
        event_type: str,
    ) -> TicketEvent | None:
        event_key = cls.ticket_event_key(
            escalation_id=escalation.id,
            transition_version=escalation.transition_version,
        )
        existing = await TicketEventRepository.get_by_event_key(db, event_key)
        if existing is not None:
            return existing

        event = TicketEvent(
            event_key=event_key,
            event_type=event_type,
            source="sla_monitor",
            ticket_id=escalation.ticket_id,
            payload={
                "escalation_id": escalation.id,
                "organization_id": escalation.organization_id,
                "milestone": escalation.milestone,
                "stage": escalation.stage,
                "status": escalation.status,
                "resolution_sla_cycle": escalation.resolution_sla_cycle,
                "due_at": escalation.due_at.isoformat() if escalation.due_at else None,
                "transition_version": escalation.transition_version,
            },
        )
        TicketEventRepository.add(db, event)
        return event

    @classmethod
    async def ensure_transition(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        milestone: EscalationMilestone,
        now: datetime,
    ) -> ServiceEscalation | None:
        """Reconcile escalation state for a ticket milestone.

        Idempotent. Returns the current escalation or None if no escalation is
        warranted. Commits escalation + event atomically.
        """
        if ticket.organization_id is None:
            return None

        organization_id = ticket.organization_id
        ticket_id = ticket.id

        # A completed milestone never warrants a new escalation. Resolve any
        # lingering active escalation (a race where the completion hook has not
        # run yet) with milestone_completed so the history stays immutable.
        if (
            (milestone == "first_response" and ticket.first_response_at is not None)
            or (milestone == "resolution" and ticket.resolved_at is not None)
        ):
            return await cls._resolve_active_if_needed(
                db,
                ticket=ticket,
                milestone=milestone,
                now=now,
                resolution_reason="milestone_completed",
            )

        cycle = ticket.resolution_sla_cycle if milestone == "resolution" else 0
        stage = cls._current_stage(ticket, milestone=milestone, now=now)

        due_at = (
            ticket.first_response_due_at
            if milestone == "first_response"
            else ticket.resolution_due_at
        )

        # No escalation needed: resolve any active escalation.
        if stage is None:
            return await cls._resolve_active_if_needed(
                db,
                ticket=ticket,
                milestone=milestone,
                now=now,
                resolution_reason="no_longer_applicable",
            )

        event_type = cls.SLA_EVENT_TYPES[milestone][stage]
        event_key = cls.event_key_for_escalation(
            organization_id=organization_id,
            ticket_id=ticket_id,
            milestone=milestone,
            stage=stage,
            resolution_sla_cycle=cycle,
            due_at=due_at,
        )

        # Try to lock an existing active escalation first.
        escalation = await ServiceEscalationRepository.get_active_for_ticket_milestone(
            db,
            ticket_id=ticket_id,
            organization_id=organization_id,
            milestone=milestone,
            for_update=True,
        )

        if escalation is None:
            # No active escalation. There may still be a lifecycle row for this
            # milestone (resolved cycle 0 after a reopen): the unique
            # (org, ticket, milestone) constraint keeps ONE row per milestone,
            # so a re-escalation must recycle that row rather than insert a
            # duplicate. A brand-new escalation is created otherwise.
            prior = (
                await ServiceEscalationRepository.get_for_milestone_any_status(
                    db,
                    ticket_id=ticket_id,
                    organization_id=organization_id,
                    milestone=milestone,
                )
            )
            if prior is not None:
                escalation = await cls._reconcile_existing(
                    db,
                    escalation=prior,
                    stage=stage,
                    due_at=due_at,
                    triggered_at=now,
                    cycle=cycle,
                    event_key=event_key,
                    event_type=event_type,
                )
            else:
                # Create new escalation. Unique constraint on (org, ticket,
                # milestone) protects against concurrent creation.
                escalation = ServiceEscalation(
                    organization_id=organization_id,
                    ticket_id=ticket_id,
                    milestone=milestone,
                    stage=stage,
                    resolution_sla_cycle=cycle,
                    event_key=event_key,
                    due_at=due_at,
                    triggered_at=now,
                    status="open",
                    transition_version=1,
                )
                await ServiceEscalationRepository.add(db, escalation)
                try:
                    await ServiceEscalationRepository.flush(db)
                except IntegrityError:
                    await db.rollback()
                    escalation = (
                        await ServiceEscalationRepository.get_for_milestone_any_status(
                            db,
                            ticket_id=ticket_id,
                            organization_id=organization_id,
                            milestone=milestone,
                        )
                    )
                    if escalation is None:
                        return None
                    # A concurrent worker won the race: reconcile the row.
                    escalation = await cls._reconcile_existing(
                        db,
                        escalation=escalation,
                        stage=stage,
                        due_at=due_at,
                        triggered_at=now,
                        cycle=cycle,
                        event_key=event_key,
                        event_type=event_type,
                    )
        else:
            escalation = await cls._transition_existing(
                db,
                escalation=escalation,
                stage=stage,
                due_at=due_at,
                triggered_at=now,
                event_type=event_type,
            )

        # Exactly one TicketEvent is recorded per transition (the current
        # transition_version), so a worker retry stays deterministic: the same
        # event_key resolves to the same persisted event and no second event is
        # created.
        await cls._ensure_event(db, escalation=escalation, event_type=event_type)
        await db.commit()
        await db.refresh(escalation)
        record_escalation_transition(
            milestone=escalation.milestone,
            stage=escalation.stage,
        )
        log.info(
            "sla_escalation_transitioned",
            escalation_id=escalation.id,
            organization_id=escalation.organization_id,
            ticket_id=escalation.ticket_id,
            milestone=escalation.milestone,
            stage=escalation.stage,
            transition_version=escalation.transition_version,
        )
        return escalation

    @classmethod
    async def _transition_existing(
        cls,
        db: AsyncSession,
        *,
        escalation: ServiceEscalation,
        stage: EscalationStage,
        due_at: datetime | None,
        triggered_at: datetime,
        event_type: str,
    ) -> ServiceEscalation:
        # Stage or deadline changed -> transition. Same stage with same deadline
        # and still active is a no-op.
        if (
            escalation.stage == stage
            and escalation.due_at == due_at
            and escalation.status in ("open", "acknowledged")
        ):
            return escalation

        # Breach always supersedes a due-soon warning on the same milestone.
        # The superseded due-soon escalation is resolved and its resolution
        # event is recorded; the caller records the new stage's event for the
        # reopened transition_version.
        if stage == "breached" and escalation.stage == "due_soon":
            await ServiceEscalationRepository.resolve(
                db,
                escalation=escalation,
                resolved_at=triggered_at,
                resolution_reason="escalated_to_breach",
            )
            await cls._ensure_event(
                db,
                escalation=escalation,
                event_type="sla.escalation.resolved",
            )

        # Reopen/create the active escalation for the new stage/deadline.
        await ServiceEscalationRepository.reopen(
            db,
            escalation=escalation,
            stage=stage,
            due_at=due_at,
            triggered_at=triggered_at,
        )
        return escalation

    @classmethod
    async def _reconcile_existing(
        cls,
        db: AsyncSession,
        *,
        escalation: ServiceEscalation,
        stage: EscalationStage,
        due_at: datetime | None,
        triggered_at: datetime,
        cycle: int,
        event_key: str,
        event_type: str,
    ) -> ServiceEscalation:
        """Bring an existing escalation row in line with the current state.

        Called when `get_active_for_ticket_milestone` found nothing but a
        lifecycle row still exists for the milestone (the unique
        (org, ticket, milestone) slot).

        - An active row is the concurrent-create race: transition it in place
          (its cycle is already current — a reopen always bumps the ticket
          cycle before the worker re-runs).
        - A resolved row is a re-escalation after reopen (or any earlier
          resolution): recycle the same slot for the new stage AND let it adopt
          the CURRENT resolution cycle, so the history stays on one row.
        """
        if escalation.status in ("open", "acknowledged"):
            return await cls._transition_existing(
                db,
                escalation=escalation,
                stage=stage,
                due_at=due_at,
                triggered_at=triggered_at,
                event_type=event_type,
            )
        await ServiceEscalationRepository.reopen(
            db,
            escalation=escalation,
            stage=stage,
            due_at=due_at,
            triggered_at=triggered_at,
        )
        escalation.resolution_sla_cycle = cycle
        escalation.event_key = event_key
        return escalation

    @classmethod
    async def _resolve_active_if_needed(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        milestone: EscalationMilestone,
        now: datetime,
        resolution_reason: str,
    ) -> ServiceEscalation | None:
        if ticket.organization_id is None:
            return None

        escalation = await ServiceEscalationRepository.get_active_for_ticket_milestone(
            db,
            ticket_id=ticket.id,
            organization_id=ticket.organization_id,
            milestone=milestone,
            for_update=True,
        )
        if escalation is None:
            return None

        await ServiceEscalationRepository.resolve(
            db,
            escalation=escalation,
            resolved_at=now,
            resolution_reason=resolution_reason,
        )
        event_type = "sla.escalation.resolved"
        await cls._ensure_event(db, escalation=escalation, event_type=event_type)
        await db.commit()
        await db.refresh(escalation)
        return escalation

    @classmethod
    async def resolve_for_milestone_completion(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        milestone: EscalationMilestone,
        now: datetime,
        resolution_reason: str = "milestone_completed",
    ) -> ServiceEscalation | None:
        return await cls._resolve_active_if_needed(
            db,
            ticket=ticket,
            milestone=milestone,
            now=now,
            resolution_reason=resolution_reason,
        )

    @classmethod
    async def resolve_for_deadline_recalculated(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        milestone: EscalationMilestone,
        old_due_at: datetime,
        now: datetime,
    ) -> ServiceEscalation | None:
        if ticket.organization_id is None:
            return None

        escalation = await ServiceEscalationRepository.get_active_for_ticket_milestone(
            db,
            ticket_id=ticket.id,
            organization_id=ticket.organization_id,
            milestone=milestone,
            for_update=True,
        )
        if escalation is None or escalation.due_at != old_due_at:
            return None

        await ServiceEscalationRepository.resolve(
            db,
            escalation=escalation,
            resolved_at=now,
            resolution_reason="deadline_recalculated",
        )
        await cls._ensure_event(
            db,
            escalation=escalation,
            event_type="sla.escalation.resolved",
        )
        await db.commit()
        await db.refresh(escalation)
        return escalation

    @classmethod
    async def acknowledge_for_tenant(
        cls,
        db: AsyncSession,
        *,
        escalation_id: int,
        organization_id: int,
        subject: str,
        now: datetime,
    ) -> ServiceEscalation | None:
        escalation = await ServiceEscalationRepository.get_by_id_for_tenant(
            db,
            escalation_id=escalation_id,
            organization_id=organization_id,
        )
        if escalation is None:
            return None
        if escalation.status == "resolved":
            return escalation

        # Lock row for atomic acknowledge race.
        result = await db.execute(
            select(ServiceEscalation)
            .where(
                ServiceEscalation.id == escalation_id,
                ServiceEscalation.organization_id == organization_id,
                ServiceEscalation.status != "resolved",
            )
            .with_for_update()
        )
        locked = result.scalar_one_or_none()
        if locked is None:
            return None

        if locked.acknowledged_at is None:
            locked.acknowledged_at = now
            locked.acknowledged_by_subject = subject
            locked.status = "acknowledged"
            locked.transition_version += 1
            record_escalation_acknowledged(
                milestone=locked.milestone,
                stage=locked.stage,
            )
            await cls._ensure_event(
                db,
                escalation=locked,
                event_type="sla.escalation.acknowledged",
            )
            await db.commit()
            await db.refresh(locked)
        return locked
