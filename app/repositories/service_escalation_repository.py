from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_escalation import ServiceEscalation
from app.models.ticket import Ticket
from app.schemas.service_escalation import (
    EscalationWindowedSummary,
    MilestoneBucket,
    StageBucket,
)

EscalationStatus = Literal["open", "acknowledged", "resolved"]
EscalationStage = Literal["due_soon", "breached"]
EscalationMilestone = Literal["first_response", "resolution"]


class ServiceEscalationRepository:
    """Tenant-scoped persistence for SLA escalation state."""

    @staticmethod
    async def create(
        db: AsyncSession,
        escalation: ServiceEscalation,
    ) -> ServiceEscalation:
        db.add(escalation)
        await db.commit()
        await db.refresh(escalation)
        return escalation

    @staticmethod
    async def add(
        db: AsyncSession,
        escalation: ServiceEscalation,
    ) -> None:
        """Stage an escalation inside the caller's transaction (no commit)."""
        db.add(escalation)

    @staticmethod
    async def flush(
        db: AsyncSession,
    ) -> None:
        await db.flush()

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        *,
        escalation_id: int,
        organization_id: int,
    ) -> ServiceEscalation | None:
        result = await db.execute(
            select(ServiceEscalation).where(
                ServiceEscalation.id == escalation_id,
                ServiceEscalation.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_event_key_for_tenant(
        db: AsyncSession,
        *,
        event_key: str,
        organization_id: int,
    ) -> ServiceEscalation | None:
        result = await db.execute(
            select(ServiceEscalation).where(
                ServiceEscalation.event_key == event_key,
                ServiceEscalation.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_active_for_ticket_milestone(
        db: AsyncSession,
        *,
        ticket_id: int,
        organization_id: int,
        milestone: EscalationMilestone,
        for_update: bool = False,
    ) -> ServiceEscalation | None:
        stmt = (
            select(ServiceEscalation)
            .where(
                ServiceEscalation.ticket_id == ticket_id,
                ServiceEscalation.organization_id == organization_id,
                ServiceEscalation.milestone == milestone,
                ServiceEscalation.status.in_(["open", "acknowledged"]),
            )
            .order_by(ServiceEscalation.transition_version.desc())
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_for_milestone_any_status(
        db: AsyncSession,
        *,
        ticket_id: int,
        organization_id: int,
        milestone: EscalationMilestone,
    ) -> ServiceEscalation | None:
        """Return the single escalation row for a milestone, any status.

        The (organization, ticket, milestone) unique constraint guarantees at
        most one row per milestone across the whole ticket life, so this row —
        active OR resolved — is the durable slot a re-escalation must recycle
        (e.g. after a reopen starts a new resolution SLA cycle).
        """
        result = await db.execute(
            select(ServiceEscalation)
            .where(
                ServiceEscalation.ticket_id == ticket_id,
                ServiceEscalation.organization_id == organization_id,
                ServiceEscalation.milestone == milestone,
            )
            .order_by(ServiceEscalation.transition_version.desc())
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        status: EscalationStatus | None = None,
        stage: EscalationStage | None = None,
        milestone: EscalationMilestone | None = None,
        queue_id: int | None = None,
        assigned_subject: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ServiceEscalation]:
        predicates = [ServiceEscalation.organization_id == organization_id]
        if status is not None:
            predicates.append(ServiceEscalation.status == status)
        if stage is not None:
            predicates.append(ServiceEscalation.stage == stage)
        if milestone is not None:
            predicates.append(ServiceEscalation.milestone == milestone)
        if queue_id is not None:
            predicates.append(Ticket.service_queue_id == queue_id)
        if assigned_subject is not None:
            predicates.append(Ticket.assigned_subject == assigned_subject)

        stmt = (
            select(ServiceEscalation)
            .join(Ticket, Ticket.id == ServiceEscalation.ticket_id)
            .where(*predicates)
            .order_by(
                ServiceEscalation.triggered_at.asc(),
                ServiceEscalation.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def summary_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> dict:
        from sqlalchemy import func

        result = await db.execute(
            select(
                func.count().label("total"),
                func.coalesce(
                    func.sum(
                        case((ServiceEscalation.status.in_(["open", "acknowledged"]), 1), else_=0)
                    ),
                    0,
                ).label("active"),
                func.coalesce(
                    func.sum(
                        case((ServiceEscalation.status == "open", 1), else_=0)
                    ),
                    0,
                ).label("unacknowledged"),
                func.coalesce(
                    func.sum(
                        case((ServiceEscalation.stage == "due_soon", 1), else_=0)
                    ),
                    0,
                ).label("due_soon"),
                func.coalesce(
                    func.sum(
                        case((ServiceEscalation.stage == "breached", 1), else_=0)
                    ),
                    0,
                ).label("breached"),
            ).where(ServiceEscalation.organization_id == organization_id)
        )
        row = result.one()
        return {
            "total": int(row.total),
            "active": int(row.active),
            "unacknowledged": int(row.unacknowledged),
            "due_soon": int(row.due_soon),
            "breached": int(row.breached),
        }

    @staticmethod
    async def acknowledge_for_tenant(
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
        if escalation.acknowledged_at is None:
            escalation.acknowledged_at = now
            escalation.acknowledged_by_subject = subject
            escalation.status = "acknowledged"
            escalation.transition_version += 1
        return escalation

    @staticmethod
    async def resolve(
        db: AsyncSession,
        *,
        escalation: ServiceEscalation,
        resolved_at: datetime,
        resolution_reason: str,
    ) -> ServiceEscalation:
        if escalation.status == "resolved":
            return escalation
        escalation.status = "resolved"
        escalation.resolved_at = resolved_at
        escalation.resolution_reason = resolution_reason
        escalation.transition_version += 1
        return escalation

    @staticmethod
    async def reopen(
        db: AsyncSession,
        *,
        escalation: ServiceEscalation,
        stage: EscalationStage,
        due_at: datetime | None,
        triggered_at: datetime,
    ) -> ServiceEscalation:
        escalation.status = "open"
        escalation.stage = stage
        escalation.due_at = due_at
        escalation.triggered_at = triggered_at
        escalation.resolved_at = None
        escalation.resolution_reason = None
        escalation.acknowledged_at = None
        escalation.acknowledged_by_subject = None
        escalation.transition_version += 1
        return escalation

    @staticmethod
    async def transition_stage(
        db: AsyncSession,
        *,
        escalation: ServiceEscalation,
        stage: EscalationStage,
        due_at: datetime | None,
        triggered_at: datetime,
    ) -> ServiceEscalation:
        escalation.stage = stage
        escalation.due_at = due_at
        escalation.triggered_at = triggered_at
        escalation.status = "open"
        escalation.acknowledged_at = None
        escalation.acknowledged_by_subject = None
        escalation.transition_version += 1
        return escalation

    # Internal/global methods (explicitly unscoped - worker-only)

    @staticmethod
    async def list_active_unscoped(
        db: AsyncSession,
        *,
        limit: int = 500,
    ) -> list[ServiceEscalation]:
        """INTERNAL-ONLY: active escalations for scanner revalidation.

        Binds no tenant and must never be called from human/public routes.
        """
        result = await db.execute(
            select(ServiceEscalation)
            .where(ServiceEscalation.status.in_(["open", "acknowledged"]))
            .order_by(ServiceEscalation.updated_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())


    @staticmethod
    async def windowed_summary_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        now: datetime,
        days: int,
    ) -> EscalationWindowedSummary:
        from sqlalchemy import func

        window_start = now - timedelta(days=days)

        cohort_where = (
            ServiceEscalation.organization_id == organization_id,
            ServiceEscalation.triggered_at >= window_start,
        )

        header = await db.execute(
            select(
                func.count().label("triggered_in_window"),
                func.coalesce(
                    func.sum(case((ServiceEscalation.stage == "breached", 1), else_=0)),
                    0,
                ).label("breached_in_window"),
                func.coalesce(
                    func.sum(case((ServiceEscalation.stage == "due_soon", 1), else_=0)),
                    0,
                ).label("due_soon_in_window"),
                func.coalesce(
                    func.sum(
                        case(
                            (ServiceEscalation.status == "acknowledged", 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("acknowledged_in_window"),
            ).where(*cohort_where)
        )
        head = header.one()

        # The acknowledgment KPI measures ACKNOWLEDGED escalations only:
        # unacknowledged rows are not "0-minute acknowledgments" and must not
        # drag the mean down. The raw AVG is kept so a genuine 0-minute
        # acknowledgment reports as 0.0; an absent avg (no acknowledged rows in
        # the window) surfaces as None at the serialization boundary.
        head_where = cohort_where + (
            ServiceEscalation.acknowledged_at.is_not(None),
        )
        avg = (
            await db.execute(
                select(
                    func.avg(
                        func.extract(
                            "epoch",
                            ServiceEscalation.acknowledged_at
                            - ServiceEscalation.triggered_at,
                        )
                        / 60
                    ).label("average_acknowledgement_minutes"),
                ).where(*head_where)
            )
        ).scalar_one()

        current_where = (
            ServiceEscalation.organization_id == organization_id,
            ServiceEscalation.resolved_at.is_(None),
        )
        current_row = (
            await db.execute(
                select(
                    func.count().label("currently_active"),
                    func.coalesce(
                        func.sum(
                            case((ServiceEscalation.acknowledged_at.is_(None), 1), else_=0)
                        ),
                        0,
                    ).label("currently_unacknowledged"),
                ).where(*current_where)
            )
        ).one()

        # Bucket snapshots are bucket-specific: a milestone/stage bucket counts
        # exactly the unresolved rows in ITS cohort, never the org-wide totals.
        # Zero-filled buckets report their own snapshot (0 when no row of that
        # kind exists at all, or nonzero when old active rows exist). This keeps
        # `by_milestone[resolution].currently_active` truthful even while the
        # resolution window is empty.
        milestone_snapshot = {
            row.milestone: row
            for row in (
                await db.execute(
                    select(
                        ServiceEscalation.milestone.label("milestone"),
                        func.count().label("currently_active"),
                        func.coalesce(
                            func.sum(case((ServiceEscalation.acknowledged_at.is_(None), 1), else_=0)),
                            0,
                        ).label("currently_unacknowledged"),
                    ).where(
                        ServiceEscalation.organization_id == organization_id,
                        ServiceEscalation.resolved_at.is_(None),
                    ).group_by(ServiceEscalation.milestone)
                )
            ).all()
        }
        stage_snapshot = {
            row.stage: row
            for row in (
                await db.execute(
                    select(
                        ServiceEscalation.stage.label("stage"),
                        func.count().label("currently_active"),
                        func.coalesce(
                            func.sum(case((ServiceEscalation.acknowledged_at.is_(None), 1), else_=0)),
                            0,
                        ).label("currently_unacknowledged"),
                    ).where(
                        ServiceEscalation.organization_id == organization_id,
                        ServiceEscalation.resolved_at.is_(None),
                    ).group_by(ServiceEscalation.stage)
                )
            ).all()
        }

        def _snapshot(key: str, snapshots: dict) -> tuple[int, int]:
            row = snapshots.get(key)
            if row is None:
                return 0, 0
            return row.currently_active, row.currently_unacknowledged

        milestone_rows = (
            await db.execute(
                select(
                    ServiceEscalation.milestone.label("milestone"),
                    func.count().label("triggered_in_window"),
                    func.coalesce(
                        func.sum(case((ServiceEscalation.stage == "breached", 1), else_=0)),
                        0,
                    ).label("breached_in_window"),
                    func.coalesce(
                        func.sum(case((ServiceEscalation.stage == "due_soon", 1), else_=0)),
                        0,
                    ).label("due_soon_in_window"),
                    func.coalesce(
                        func.sum(
                            case(
                                (ServiceEscalation.status == "acknowledged", 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("acknowledged_in_window"),
                ).where(*cohort_where).group_by(ServiceEscalation.milestone)
            )
        ).all()
        kernel_milestones = {"first_response", "resolution"}
        by_milestone = []
        for row in milestone_rows:
            active, unacked = _snapshot(row.milestone, milestone_snapshot)
            by_milestone.append(
                MilestoneBucket(
                    milestone=row.milestone,
                    triggered_in_window=row.triggered_in_window,
                    breached_in_window=row.breached_in_window,
                    due_soon_in_window=row.due_soon_in_window,
                    acknowledged_in_window=row.acknowledged_in_window,
                    currently_active=active,
                    currently_unacknowledged=unacked,
                )
            )
        for milestone in sorted(kernel_milestones - {r.milestone for r in milestone_rows}):
            active, unacked = _snapshot(milestone, milestone_snapshot)
            by_milestone.append(
                MilestoneBucket(
                    milestone=milestone,
                    triggered_in_window=0,
                    breached_in_window=0,
                    due_soon_in_window=0,
                    acknowledged_in_window=0,
                    currently_active=active,
                    currently_unacknowledged=unacked,
                )
            )

        stage_rows = (
            await db.execute(
                select(
                    ServiceEscalation.stage.label("stage"),
                    func.count().label("triggered_in_window"),
                    func.coalesce(
                        func.sum(case((ServiceEscalation.stage == "breached", 1), else_=0)),
                        0,
                    ).label("breached_in_window"),
                    func.coalesce(
                        func.sum(case((ServiceEscalation.stage == "due_soon", 1), else_=0)),
                        0,
                    ).label("due_soon_in_window"),
                    func.coalesce(
                        func.sum(
                            case(
                                (ServiceEscalation.status == "acknowledged", 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("acknowledged_in_window"),
                ).where(*cohort_where).group_by(ServiceEscalation.stage)
            )
        ).all()
        bounded_stages = {"due_soon", "breached"}
        by_stage = []
        for row in stage_rows:
            active, unacked = _snapshot(row.stage, stage_snapshot)
            by_stage.append(
                StageBucket(
                    stage=row.stage,
                    triggered_in_window=row.triggered_in_window,
                    breached_in_window=row.breached_in_window,
                    due_soon_in_window=row.due_soon_in_window,
                    acknowledged_in_window=row.acknowledged_in_window,
                    currently_active=active,
                    currently_unacknowledged=unacked,
                )
            )
        for stage in sorted(bounded_stages - {r.stage for r in stage_rows}):
            active, unacked = _snapshot(stage, stage_snapshot)
            by_stage.append(
                StageBucket(
                    stage=stage,
                    triggered_in_window=0,
                    breached_in_window=0,
                    due_soon_in_window=0,
                    acknowledged_in_window=0,
                    currently_active=active,
                    currently_unacknowledged=unacked,
                )
            )

        return EscalationWindowedSummary(
            days=days,
            triggered_in_window=head.triggered_in_window,
            breached_in_window=head.breached_in_window,
            due_soon_in_window=head.due_soon_in_window,
            acknowledged_in_window=head.acknowledged_in_window,
            currently_active=current_row.currently_active,
            currently_unacknowledged=current_row.currently_unacknowledged,
            average_acknowledgement_minutes=None if avg is None else round(float(avg), 2),
            by_milestone=by_milestone,
            by_stage=by_stage,
        )
