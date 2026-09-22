from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sla_policy import SLAPolicy


class SLAPolicyRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        policy: SLAPolicy,
    ) -> SLAPolicy:
        db.add(policy)
        await db.commit()
        await db.refresh(policy)
        return policy

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        policy_id: int,
        organization_id: int,
    ) -> SLAPolicy | None:
        result = await db.execute(
            select(SLAPolicy).where(
                SLAPolicy.id == policy_id,
                SLAPolicy.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> list[SLAPolicy]:
        result = await db.execute(
            select(SLAPolicy)
            .where(SLAPolicy.organization_id == organization_id)
            .order_by(SLAPolicy.name.asc(), SLAPolicy.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_default_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> SLAPolicy | None:
        result = await db.execute(
            select(SLAPolicy).where(
                SLAPolicy.organization_id == organization_id,
                SLAPolicy.enabled.is_(True),
                SLAPolicy.is_default.is_(True),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_for_tenant(
        db: AsyncSession,
        policy: SLAPolicy,
        changes: dict,
        organization_id: int,
    ) -> SLAPolicy:
        if policy.organization_id != organization_id:
            raise ValueError("SLA policy does not belong to this organization")

        for field, value in changes.items():
            setattr(policy, field, value)

        await db.commit()
        await db.refresh(policy)
        return policy
