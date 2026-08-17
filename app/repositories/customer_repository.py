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

    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        customer_id: int,
    ) -> Customer | None:
        result = await db.execute(
            select(Customer).where(
                Customer.id == customer_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list(
        db: AsyncSession,
    ) -> list[Customer]:
        result = await db.execute(
            select(Customer).order_by(
                Customer.created_at.desc()
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_email(
        db: AsyncSession,
        email: str,
    ) -> Customer | None:

        result = await db.execute(
            select(Customer).where(
                Customer.email == email
            )
        )

        return result.scalar_one_or_none()

    