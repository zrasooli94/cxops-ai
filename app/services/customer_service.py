from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository
from app.schemas.customer import CustomerCreate


class CustomerService:
    @staticmethod
    async def create(
        db: AsyncSession,
        data: CustomerCreate,
    ) -> Customer:
        customer = Customer(
            name=data.name,
            email=str(data.email),
            phone=data.phone,
            organization_id=data.organization_id,
            external_id=data.external_id,
        )

        return await CustomerRepository.create(
            db,
            customer,
        )
