from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket_event import TicketEvent
from datetime import datetime, timezone

class TicketEventRepository:

    @staticmethod
    async def get_by_event_key(
        db: AsyncSession,
        event_key: str,
    ) -> TicketEvent | None:

        result = await db.execute(
            select(TicketEvent).where(
                TicketEvent.event_key == event_key
            )
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

    @staticmethod
    async def list(
        db: AsyncSession,
        limit: int = 100,
    ) -> list[TicketEvent]:

        result = await db.execute(
            select(TicketEvent)
            .order_by(TicketEvent.created_at.desc())
            .limit(limit)
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

    