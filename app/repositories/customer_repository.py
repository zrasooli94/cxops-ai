from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer


class CustomerRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        customer: Customer,
    ) -> Customer:
        db.add(customer)
        await db.commit()
        await db.refresh(customer)
        return customer

    # Tenant-safe methods (use these in API routes)

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        customer_id: int,
        organization_id: int,
    ) -> Customer | None:
        result = await db.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> list[Customer]:
        result = await db.execute(
            select(Customer)
            .where(Customer.organization_id == organization_id)
            .order_by(Customer.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_email_for_tenant(
        db: AsyncSession,
        email: str,
        organization_id: int,
    ) -> Customer | None:
        result = await db.execute(
            select(Customer).where(
                Customer.email == email,
                Customer.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    # Internal/global methods (explicitly unscoped - for webhook/internal use ONLY)
    # DO NOT call from tenant-facing API routes - use *_for_tenant variants instead.

    @staticmethod
    async def get_by_id_unscoped(
        db: AsyncSession,
        customer_id: int,
    ) -> Customer | None:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_unscoped(
        db: AsyncSession,
    ) -> list[Customer]:
        result = await db.execute(select(Customer).order_by(Customer.created_at.desc()))
        return list(result.scalars().all())
