from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sla_policy import SLAPolicy
from app.models.ticket import Ticket
from app.repositories.sla_policy_repository import SLAPolicyRepository

SLAState = Literal["not_configured", "on_track", "due_soon", "breached", "met"]

# Minutes before a deadline that a ticket is considered "due soon".
DUE_SOON_MINUTES = 30

SUPPORTED_PRIORITIES = {"low", "normal", "high", "urgent"}

OPEN_TICKET_STATUSES = ("new", "open", "pending")
RESOLVED_TICKET_STATUSES = ("solved", "closed")


class TicketSLAService:
    """SLA lifecycle for tickets.

    Phase 1H uses elapsed wall-clock time (UTC). Business hours, holidays,
    pause-on-pending, and regional calendars are NOT supported yet.
    """

    @classmethod
    def resolve_priority_target_minutes(
        cls,
        policy: SLAPolicy,
        *,
        metric: Literal["first_response", "resolution"],
        priority: str,
    ) -> int | None:
        """Return the target minutes from a policy for a metric/priority pair.

        Unknown legacy priorities fall back to 'normal' ONLY if the project
        already normalizes legacy values (it does via Ticket.priority default).
        """
        normalized = priority.lower().strip() if priority else "normal"
        if normalized not in SUPPORTED_PRIORITIES:
            normalized = "normal"
        return policy.target_minutes(metric=metric, priority=normalized)

    @classmethod
    def deadline_for(
        cls,
        policy: SLAPolicy,
        *,
        metric: Literal["first_response", "resolution"],
        priority: str,
        created_at: datetime,
    ) -> datetime | None:
        """Calculate a deadline from ticket.created_at and the policy target."""
        target_minutes = cls.resolve_priority_target_minutes(
            policy,
            metric=metric,
            priority=priority,
        )
        if target_minutes is None:
            return None
        return created_at + timedelta(minutes=target_minutes)

    @classmethod
    async def apply_policy_to_ticket(
        cls,
        db: AsyncSession,
        ticket: Ticket,
        policy: SLAPolicy | None,
    ) -> None:
        """Set the ticket's SLA policy and calculate/refresh deadlines.

        Deadlines are always computed from ticket.created_at, never from the
        current time, so queue/policy/priority changes do not reset the SLA
        clock. Existing completed milestones are preserved.
        """
        from app.services.service_escalation_service import (
            ServiceEscalationService,
        )

        old_first_due = ticket.first_response_due_at
        old_resolution_due = ticket.resolution_due_at

        if policy is None:
            ticket.sla_policy_id = None
            # Preserve historical completed deadlines; only clear active ones.
            if ticket.first_response_at is None:
                ticket.first_response_due_at = None
            if ticket.resolved_at is None:
                ticket.resolution_due_at = None
            return

        ticket.sla_policy_id = policy.id

        # Active milestones recalculate from the original clock anchor.
        # Completed milestones keep their historical deadline snapshot.
        if ticket.first_response_at is None:
            ticket.first_response_due_at = cls.deadline_for(
                policy,
                metric="first_response",
                priority=ticket.priority,
                created_at=ticket.created_at,
            )

        if ticket.status in RESOLVED_TICKET_STATUSES:
            if ticket.resolved_at is None:
                ticket.resolved_at = datetime.now(UTC)
            # Preserve the historical resolution deadline for attainment calcs.
        else:
            ticket.resolution_due_at = cls.deadline_for(
                policy,
                metric="resolution",
                priority=ticket.priority,
                created_at=ticket.created_at,
            )

        now = datetime.now(UTC)
        if (
            ticket.first_response_at is None
            and old_first_due is not None
            and ticket.first_response_due_at != old_first_due
        ):
            await ServiceEscalationService.resolve_for_deadline_recalculated(
                db,
                ticket=ticket,
                milestone="first_response",
                old_due_at=old_first_due,
                now=now,
            )

        if (
            ticket.resolved_at is None
            and old_resolution_due is not None
            and ticket.resolution_due_at != old_resolution_due
        ):
            await ServiceEscalationService.resolve_for_deadline_recalculated(
                db,
                ticket=ticket,
                milestone="resolution",
                old_due_at=old_resolution_due,
                now=now,
            )

    @classmethod
    async def select_policy_for_ticket(
        cls,
        db: AsyncSession,
        ticket: Ticket,
    ) -> SLAPolicy | None:
        """Choose the applicable SLA policy for a ticket.

        Order of precedence:
        1. Queue's explicit SLA policy (if queue is set and has one)
        2. Tenant default SLA policy
        3. None
        """
        if ticket.organization_id is None:
            return None

        if (
            ticket.service_queue_id is not None
            and ticket.service_queue is not None
            and ticket.service_queue.sla_policy_id is not None
        ):
            return ticket.service_queue.sla_policy

        return await SLAPolicyRepository.get_default_for_tenant(
            db,
            organization_id=ticket.organization_id,
        )

    @classmethod
    async def ensure_ticket_policy_and_deadlines(
        cls,
        db: AsyncSession,
        ticket: Ticket,
    ) -> None:
        """Resolve and apply the right SLA policy for a ticket in one call."""
        policy = await cls.select_policy_for_ticket(db, ticket)
        await cls.apply_policy_to_ticket(db, ticket, policy)

    @classmethod
    async def recalculate_for_priority_change(
        cls,
        db: AsyncSession,
        ticket: Ticket,
    ) -> None:
        """Recalculate unfinished deadlines when priority changes.

        Completed milestones and the original clock anchor are preserved.
        """
        if ticket.organization_id is None:
            return

        policy = None
        if ticket.sla_policy_id is not None:
            policy = await SLAPolicyRepository.get_by_id_for_tenant(
                db,
                policy_id=ticket.sla_policy_id,
                organization_id=ticket.organization_id,
            )
        if policy is None:
            policy = await cls.select_policy_for_ticket(db, ticket)

        await cls.apply_policy_to_ticket(db, ticket, policy)

    @classmethod
    async def record_first_response(
        cls,
        db: AsyncSession,
        ticket: Ticket,
        *,
        responded_at: datetime,
    ) -> None:
        """Record the earliest known successful public outbound response."""
        if ticket.first_response_at is not None and ticket.first_response_at <= responded_at:
            return

        ticket.first_response_at = responded_at
        # Historical first_response_due_at is preserved for met/breached eval.
        await cls.resolve_escalation_for_milestone_completion(
            db,
            ticket,
            milestone="first_response",
            now=responded_at,
        )

    @classmethod
    async def resolve_escalation_for_milestone_completion(
        cls,
        db,
        ticket,
        *,
        milestone,
        now,
    ):
        from app.services.service_escalation_service import (
            ServiceEscalationService,
        )

        await ServiceEscalationService.resolve_for_milestone_completion(
            db,
            ticket=ticket,
            milestone=milestone,
            now=now,
        )

    @classmethod
    async def record_resolution(
        cls,
        db: AsyncSession,
        ticket: Ticket,
        *,
        resolved_at: datetime | None = None,
    ) -> None:
        """Record resolution when a ticket enters a resolved status."""
        if ticket.status not in RESOLVED_TICKET_STATUSES:
            return

        if ticket.resolved_at is not None:
            return

        ticket.resolved_at = resolved_at or datetime.now(UTC)
        # Historical resolution_due_at is preserved for met/breached eval.
        await cls.resolve_escalation_for_milestone_completion(
            db,
            ticket,
            milestone="resolution",
            now=ticket.resolved_at,
        )

    @classmethod
    async def handle_reopen(
        cls,
        db: AsyncSession,
        ticket: Ticket,
    ) -> None:
        """Clear resolved_at when a solved/closed ticket returns to open work.

        The resolution deadline remains anchored to ticket.created_at and is
        recalculated from the active policy.
        """
        if ticket.status in RESOLVED_TICKET_STATUSES:
            return

        ticket.resolved_at = None
        if ticket.organization_id is None:
            return

        now = datetime.now(UTC)

        # Supersede the prior resolution-cycle escalation before recalculating.
        from app.services.service_escalation_service import (
            ServiceEscalationService,
        )

        await ServiceEscalationService.resolve_for_milestone_completion(
            db,
            ticket=ticket,
            milestone="resolution",
            now=now,
            resolution_reason="ticket_reopened",
        )

        policy = None
        if ticket.sla_policy_id is not None:
            policy = await SLAPolicyRepository.get_by_id_for_tenant(
                db,
                policy_id=ticket.sla_policy_id,
                organization_id=ticket.organization_id,
            )
        if policy is None:
            policy = await cls.select_policy_for_ticket(db, ticket)

        await cls.apply_policy_to_ticket(db, ticket, policy)

    @classmethod
    def state_for(
        cls,
        *,
        due_at: datetime | None,
        completed_at: datetime | None,
        now: datetime,
    ) -> SLAState:
        """Derive deterministic SLA state for a single milestone."""
        if due_at is None:
            return "not_configured"

        if completed_at is not None:
            return "met" if completed_at <= due_at else "breached"

        if now > due_at:
            return "breached"

        if due_at - timedelta(minutes=DUE_SOON_MINUTES) <= now:
            return "due_soon"

        return "on_track"

    @classmethod
    def first_response_state(
        cls,
        ticket: Ticket,
        *,
        now: datetime,
    ) -> SLAState:
        return cls.state_for(
            due_at=ticket.first_response_due_at,
            completed_at=ticket.first_response_at,
            now=now,
        )

    @classmethod
    def resolution_state(
        cls,
        ticket: Ticket,
        *,
        now: datetime,
    ) -> SLAState:
        return cls.state_for(
            due_at=ticket.resolution_due_at,
            completed_at=ticket.resolved_at,
            now=now,
        )

    @classmethod
    def overall_state(
        cls,
        ticket: Ticket,
        *,
        now: datetime,
    ) -> SLAState:
        """Overall SLA state is the worst of the two milestones."""
        first = cls.first_response_state(ticket, now=now)
        resolution = cls.resolution_state(ticket, now=now)

        order: list[SLAState] = [
            "met",
            "not_configured",
            "on_track",
            "due_soon",
            "breached",
        ]
        return max(first, resolution, key=lambda s: order.index(s))
