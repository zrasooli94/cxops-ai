from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository
from app.schemas.customer import CustomerCreate


class CustomerService:
    @staticmethod
    async def create_for_tenant(
        db: AsyncSession,
        data: CustomerCreate,
        organization_id: int,
    ) -> Customer:
        # organization_id comes from CurrentTenant, never from client payload
        customer = Customer(
            name=data.name,
            email=str(data.email),
            phone=data.phone,
            organization_id=organization_id,
            external_id=data.external_id,
        )

        return await CustomerRepository.create(
            db,
            customer,
        )

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> list[Customer]:
        return await CustomerRepository.list_for_tenant(db, organization_id)

    @staticmethod
    async def get_for_tenant(
        db: AsyncSession,
        customer_id: int,
        organization_id: int,
    ) -> Customer | None:
        return await CustomerRepository.get_by_id_for_tenant(
            db, customer_id, organization_id
        )
