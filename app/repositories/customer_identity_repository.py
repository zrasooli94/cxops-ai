from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.customer_identity import CustomerIdentity


class CustomerIdentityRepository:
    @staticmethod
    async def get_by_provider_identity_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        provider: str,
        identity_type: str,
        normalized_identifier: str,
    ) -> CustomerIdentity | None:
        result = await db.execute(
            select(CustomerIdentity)
            .where(
                CustomerIdentity.organization_id == organization_id,
                CustomerIdentity.provider == provider,
                CustomerIdentity.identity_type == identity_type,
                CustomerIdentity.normalized_identifier == normalized_identifier,
            )
            .options(selectinload(CustomerIdentity.customer))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_customer_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        customer_id: int,
        limit: int = 100,
    ) -> list[CustomerIdentity]:
        result = await db.execute(
            select(CustomerIdentity)
            .where(
                CustomerIdentity.organization_id == organization_id,
                CustomerIdentity.customer_id == customer_id,
            )
            .order_by(
                CustomerIdentity.provider.asc(),
                CustomerIdentity.identity_type.asc(),
                CustomerIdentity.normalized_identifier.asc(),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def count_for_customer_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        customer_id: int,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(CustomerIdentity)
            .where(
                CustomerIdentity.organization_id == organization_id,
                CustomerIdentity.customer_id == customer_id,
            )
        )
        return int(result.scalar_one())

    @staticmethod
    async def create(
        db: AsyncSession,
        identity: CustomerIdentity,
    ) -> CustomerIdentity:
        db.add(identity)
        await db.commit()
        await db.refresh(identity)
        return identity
