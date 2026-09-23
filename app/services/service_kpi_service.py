from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_queue import ServiceQueue
from app.models.ticket import Ticket

OPEN_TICKET_STATUSES = ("new", "open", "pending")
RESOLVED_TICKET_STATUSES = ("solved", "closed")


class ServiceKPIService:
    """Tenant-scoped service KPI calculations.

    Every KPI query includes organization_id in SQL. No global aggregation.
    Population definitions are explicit in code and tests.
    """

    @classmethod
    def _window_bounds(
        cls,
        *,
        now: datetime,
        days: int,
    ) -> tuple[datetime, datetime]:
        start = now - timedelta(days=days)
        return start, now

    @classmethod
    async def kpis_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        days: int = 7,
        now: datetime | None = None,
    ) -> dict:
        if now is None:
            now = datetime.now(UTC)

        start, end = cls._window_bounds(now=now, days=days)

        # Current snapshot
        current = await cls._current_snapshot(db, organization_id=organization_id, now=now)

        # Window-created tickets
        created_result = await db.execute(
            select(func.count())
            .select_from(Ticket)
            .where(
                Ticket.organization_id == organization_id,
                Ticket.created_at >= start,
                Ticket.created_at <= end,
            )
        )
        tickets_created_in_window = int(created_result.scalar_one())

        # First responses completed in window
        first_response_result = await db.execute(
            select(
                func.count().label("count"),
                func.coalesce(
                    func.avg(
                        func.extract(
                            "epoch",
                            Ticket.first_response_at - Ticket.created_at,
                        )
                        / 60
                    ),
                    0,
                ).label("avg_minutes"),
                func.coalesce(
                    func.sum(case((Ticket.first_response_at <= Ticket.first_response_due_at, 1), else_=0)),
                    0,
                ).label("met"),
                func.coalesce(
                    func.sum(case((Ticket.first_response_at > Ticket.first_response_due_at, 1), else_=0)),
                    0,
                ).label("breached"),
            )
            .where(
                Ticket.organization_id == organization_id,
                Ticket.first_response_at.isnot(None),
                Ticket.first_response_at >= start,
                Ticket.first_response_at <= end,
                Ticket.first_response_due_at.isnot(None),
            )
        )
        fr_row = first_response_result.one()
        first_response_completed = int(fr_row[0])
        avg_first_response_minutes = (
            float(fr_row.avg_minutes) if first_response_completed > 0 else None
        )
        first_response_met = int(fr_row.met)
        first_response_breached = int(fr_row.breached)
        first_response_attainment = (
            round(first_response_met / first_response_completed * 100, 2)
            if first_response_completed > 0
            else None
        )

        # Resolved in window
        resolved_result = await db.execute(
            select(
                func.count().label("count"),
                func.coalesce(
                    func.avg(
                        func.extract(
                            "epoch",
                            Ticket.resolved_at - Ticket.created_at,
                        )
                        / 60
                    ),
                    0,
                ).label("avg_minutes"),
                func.coalesce(
                    func.sum(case((Ticket.resolved_at <= Ticket.resolution_due_at, 1), else_=0)),
                    0,
                ).label("met"),
                func.coalesce(
                    func.sum(case((Ticket.resolved_at > Ticket.resolution_due_at, 1), else_=0)),
                    0,
                ).label("breached"),
            )
            .where(
                Ticket.organization_id == organization_id,
                Ticket.resolved_at.isnot(None),
                Ticket.resolved_at >= start,
                Ticket.resolved_at <= end,
                Ticket.resolution_due_at.isnot(None),
            )
        )
        res_row = resolved_result.one()
        resolved_in_window = int(res_row[0])
        avg_resolution_minutes = (
            float(res_row.avg_minutes) if resolved_in_window > 0 else None
        )
        resolution_met = int(res_row.met)
        resolution_breached = int(res_row.breached)
        resolution_attainment = (
            round(resolution_met / resolved_in_window * 100, 2)
            if resolved_in_window > 0
            else None
        )

        return {
            "period_days": days,
            "period_start": start,
            "period_end": end,
            "current_open_backlog": current["open"],
            "current_unassigned": current["unassigned"],
            "current_response_breaches": current["response_breaches"],
            "current_resolution_breaches": current["resolution_breaches"],
            "tickets_created_in_window": tickets_created_in_window,
            "first_response_completed_in_window": first_response_completed,
            "average_first_response_minutes": avg_first_response_minutes,
            "first_response_sla_met": first_response_met,
            "first_response_sla_breached": first_response_breached,
            "first_response_sla_attainment_percent": first_response_attainment,
            "resolved_in_window": resolved_in_window,
            "average_resolution_minutes": avg_resolution_minutes,
            "resolution_sla_met": resolution_met,
            "resolution_sla_breached": resolution_breached,
            "resolution_sla_attainment_percent": resolution_attainment,
        }

    @classmethod
    async def _current_snapshot(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        now: datetime,
    ) -> dict:
        result = await db.execute(
            select(
                func.coalesce(
                    func.sum(case((Ticket.status.in_(OPEN_TICKET_STATUSES), 1), else_=0)),
                    0,
                ).label("open"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(OPEN_TICKET_STATUSES)
                                & Ticket.assigned_subject.is_(None),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("unassigned"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(OPEN_TICKET_STATUSES)
                                & Ticket.first_response_due_at.isnot(None)
                                & (Ticket.first_response_due_at < now),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("response_breaches"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(OPEN_TICKET_STATUSES)
                                & Ticket.resolution_due_at.isnot(None)
                                & (Ticket.resolution_due_at < now),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("resolution_breaches"),
            )
            .where(Ticket.organization_id == organization_id)
        )
        row = result.one()
        return {
            "open": int(row.open),
            "unassigned": int(row.unassigned),
            "response_breaches": int(row.response_breaches),
            "resolution_breaches": int(row.resolution_breaches),
        }

    @classmethod
    async def _ticket_map_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        ticket_ids: list[int],
    ) -> dict[int, Ticket]:
        if not ticket_ids:
            return {}
        result = await db.execute(
            select(Ticket).where(
                Ticket.organization_id == organization_id,
                Ticket.id.in_(ticket_ids),
            )
        )
        return {ticket.id: ticket for ticket in result.scalars().all()}

    @classmethod
    async def _queue_map_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> dict[int, ServiceQueue]:
        result = await db.execute(
            select(ServiceQueue).where(
                ServiceQueue.organization_id == organization_id,
            )
        )
        return {queue.id: queue for queue in result.scalars().all()}
