from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_queue import ServiceQueue


class ServiceQueueRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        queue: ServiceQueue,
    ) -> ServiceQueue:
        db.add(queue)
        await db.commit()
        await db.refresh(queue)
        return queue

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        queue_id: int,
        organization_id: int,
    ) -> ServiceQueue | None:
        result = await db.execute(
            select(ServiceQueue).where(
                ServiceQueue.id == queue_id,
                ServiceQueue.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_key_for_tenant(
        db: AsyncSession,
        key: str,
        organization_id: int,
    ) -> ServiceQueue | None:
        result = await db.execute(
            select(ServiceQueue).where(
                ServiceQueue.key == key,
                ServiceQueue.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> list[ServiceQueue]:
        result = await db.execute(
            select(ServiceQueue)
            .where(ServiceQueue.organization_id == organization_id)
            .order_by(ServiceQueue.name.asc(), ServiceQueue.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_active_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> list[ServiceQueue]:
        result = await db.execute(
            select(ServiceQueue)
            .where(
                ServiceQueue.organization_id == organization_id,
                ServiceQueue.active.is_(True),
            )
            .order_by(ServiceQueue.name.asc(), ServiceQueue.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_default_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> ServiceQueue | None:
        result = await db.execute(
            select(ServiceQueue).where(
                ServiceQueue.organization_id == organization_id,
                ServiceQueue.active.is_(True),
                ServiceQueue.is_default.is_(True),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_for_tenant(
        db: AsyncSession,
        queue: ServiceQueue,
        changes: dict,
        organization_id: int,
    ) -> ServiceQueue:
        if queue.organization_id != organization_id:
            raise ValueError("Service queue does not belong to this organization")

        for field, value in changes.items():
            setattr(queue, field, value)

        await db.commit()
        await db.refresh(queue)
        return queue
