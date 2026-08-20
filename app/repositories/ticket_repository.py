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

    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        ticket_id: int,
    ) -> Ticket | None:
        result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))

        return result.scalar_one_or_none()

    @staticmethod
    async def list(
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
    async def update(
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
    async def get_by_external_id(
        db: AsyncSession,
        external_id: str,
    ) -> Ticket | None:

        result = await db.execute(
            select(Ticket).where(Ticket.external_id == external_id)
        )

        return result.scalar_one_or_none()
