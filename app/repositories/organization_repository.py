from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization


class OrganizationRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        organization: Organization,
    ) -> Organization:
        db.add(organization)
        await db.commit()
        await db.refresh(organization)
        return organization

    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        organization_id: int,
    ) -> Organization | None:
        result = await db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list(
        db: AsyncSession,
    ) -> list[Organization]:
        result = await db.execute(
            select(Organization).order_by(Organization.created_at.desc())
        )
        return list(result.scalars().all())
