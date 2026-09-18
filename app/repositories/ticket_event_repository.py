from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket_event import TicketEvent


class TicketEventRepository:
    @staticmethod
    async def get_by_event_key(
        db: AsyncSession,
        event_key: str,
    ) -> TicketEvent | None:

        result = await db.execute(
            select(TicketEvent).where(TicketEvent.event_key == event_key)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        event: TicketEvent,
    ) -> TicketEvent:

        db.add(event)

        await db.commit()
        await db.refresh(event)

        return event

    # Tenant-safe method for listing events of tickets in a tenant organization
    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        organization_id: int,
        limit: int = 100,
    ) -> list[TicketEvent]:

        from app.models.ticket import Ticket

        result = await db.execute(
            select(TicketEvent)
            .join(Ticket, TicketEvent.ticket_id == Ticket.id)
            .where(Ticket.organization_id == organization_id)
            .order_by(TicketEvent.created_at.desc())
            .limit(limit)
        )

        return list(result.scalars().all())

    # Tenant-safe customer-scoped reads (customer 360). The join to Ticket keeps
    # the organization predicate in SQL, so events for tickets of another tenant
    # are never visible.

    @staticmethod
    async def list_for_customer_for_tenant(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        offset: int,
        limit: int,
    ) -> list[TicketEvent]:

        from app.models.ticket import Ticket

        result = await db.execute(
            select(TicketEvent)
            .join(Ticket, TicketEvent.ticket_id == Ticket.id)
            .where(
                Ticket.customer_id == customer_id,
                Ticket.organization_id == organization_id,
            )
            .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc())
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

        from app.models.ticket import Ticket

        result = await db.execute(
            select(func.count())
            .select_from(TicketEvent)
            .join(Ticket, TicketEvent.ticket_id == Ticket.id)
            .where(
                Ticket.customer_id == customer_id,
                Ticket.organization_id == organization_id,
            )
        )

        return int(result.scalar_one())

    @staticmethod
    async def list(
        db: AsyncSession,
        limit: int = 100,
    ) -> list[TicketEvent]:

        result = await db.execute(
            select(TicketEvent).order_by(TicketEvent.created_at.desc()).limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def mark_processed(
        db: AsyncSession,
        event: TicketEvent,
    ) -> TicketEvent:

        event.processed = True
        event.processed_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(event)

        return event

    @staticmethod
    async def mark_writeback_completed(
        db: AsyncSession,
        event: TicketEvent,
    ) -> TicketEvent:

        event.writeback_completed = True
        event.writeback_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(event)

        return event
