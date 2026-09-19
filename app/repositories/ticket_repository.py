from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket

OPEN_TICKET_STATUSES = ("new", "open", "pending")
RESOLVED_TICKET_STATUSES = ("solved", "closed")


class TicketRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        ticket: Ticket,
    ) -> Ticket:
        db.add(ticket)

        await db.commit()
        await db.refresh(ticket)

        return ticket

    # Tenant-safe methods (use these in API routes)

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        ticket_id: int,
        organization_id: int,
    ) -> Ticket | None:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == ticket_id,
                Ticket.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        organization_id: int,
        offset: int = 0,
        limit: int = 100,
        customer_id: int | None = None,
    ) -> list[Ticket]:
        predicates = [Ticket.organization_id == organization_id]
        if customer_id is not None:
            predicates.append(Ticket.customer_id == customer_id)

        result = await db.execute(
            select(Ticket)
            .where(*predicates)
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .offset(offset)
            .limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def get_by_external_id_for_tenant(
        db: AsyncSession,
        external_id: str,
        organization_id: int,
    ) -> Ticket | None:

        result = await db.execute(
            select(Ticket).where(
                Ticket.external_id == external_id,
                Ticket.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    # Tenant-safe customer-scoped reads (customer 360). Both the customer and
    # the organization predicate are enforced in SQL so a cross-tenant customer
    # id can never be joined back into another tenant's tickets.

    @staticmethod
    async def list_for_customer_for_tenant(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        offset: int,
        limit: int,
    ) -> list[Ticket]:
        result = await db.execute(
            select(Ticket)
            .where(
                Ticket.customer_id == customer_id,
                Ticket.organization_id == organization_id,
            )
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .offset(offset)
            .limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def count_for_customer_for_tenant(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(Ticket)
            .where(
                Ticket.customer_id == customer_id,
                Ticket.organization_id == organization_id,
            )
        )

        return int(result.scalar_one())

    @staticmethod
    async def summarize_for_customer(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
    ) -> dict:
        """One aggregate pass over the customer's tickets."""
        statement = (
            select(
                func.count().label("total"),
                func.coalesce(
                    func.sum(
                        case((Ticket.status.in_(OPEN_TICKET_STATUSES), 1), else_=0)
                    ),
                    0,
                ).label("open"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(RESOLVED_TICKET_STATUSES),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("resolved"),
                func.max(Ticket.created_at).label("latest_ticket_at"),
                func.max(Ticket.updated_at).label("latest_interaction_at"),
            )
            .where(
                Ticket.customer_id == customer_id,
                Ticket.organization_id == organization_id,
            )
        )

        row = (await db.execute(statement)).one()

        return {
            "total": int(row.total),
            "open": int(row.open),
            "resolved": int(row.resolved),
            "latest_ticket_at": row.latest_ticket_at,
            "latest_interaction_at": row.latest_interaction_at,
        }

    @staticmethod
    async def most_recent_ticket_for_customer(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
    ) -> Ticket | None:
        result = await db.execute(
            select(Ticket)
            .where(
                Ticket.customer_id == customer_id,
                Ticket.organization_id == organization_id,
            )
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .limit(1)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def common_category_for_customer(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
    ) -> str | None:
        result = await db.execute(
            select(Ticket.category, func.count().label("count"))
            .where(
                Ticket.customer_id == customer_id,
                Ticket.organization_id == organization_id,
                Ticket.category.is_not(None),
            )
            .group_by(Ticket.category)
            .order_by(func.count().desc(), Ticket.category.asc())
            .limit(1)
        )

        row = result.first()
        return row[0] if row else None

    @staticmethod
    async def update_for_tenant(
        db: AsyncSession,
        ticket: Ticket,
        changes: dict,
        organization_id: int,
    ) -> Ticket:
        """Update only if ticket belongs to the tenant organization."""
        if ticket.organization_id != organization_id:
            raise ValueError("Ticket does not belong to this organization")

        for field, value in changes.items():
            setattr(ticket, field, value)

        await db.commit()
        await db.refresh(ticket)

        return ticket

    # Internal/global methods (explicitly unscoped - for webhook/internal use ONLY)
    # DO NOT call from tenant-facing API routes - use *_for_tenant variants instead.

    @staticmethod
    async def get_by_id_unscoped(
        db: AsyncSession,
        ticket_id: int,
    ) -> Ticket | None:
        result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))

        return result.scalar_one_or_none()

    @staticmethod
    async def list_unscoped(
        db: AsyncSession,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Ticket]:
        result = await db.execute(
            select(Ticket)
            .order_by(Ticket.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def update_unscoped(
        db: AsyncSession,
        ticket: Ticket,
        changes: dict,
    ) -> Ticket:

        for field, value in changes.items():
            setattr(ticket, field, value)

        await db.commit()
        await db.refresh(ticket)

        return ticket
