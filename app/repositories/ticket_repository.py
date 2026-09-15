from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket


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
    ) -> list[Ticket]:
        result = await db.execute(
            select(Ticket)
            .where(Ticket.organization_id == organization_id)
            .order_by(Ticket.created_at.desc())
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

    @staticmethod
    async def get_by_external_id_unscoped(
        db: AsyncSession,
        external_id: str,
    ) -> Ticket | None:

        result = await db.execute(
            select(Ticket).where(Ticket.external_id == external_id)
        )

        return result.scalar_one_or_none()
