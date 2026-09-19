from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository
from app.schemas.customer import CustomerCreate


class CustomerService:
    @staticmethod
    def _normalize_email(email: str | None) -> str | None:
        if email is None:
            return None
        normalized = email.lower().strip()
        return normalized if normalized else None

    @staticmethod
    def _email_local_part(email: str) -> str:
        return email.split("@")[0]

    @staticmethod
    async def resolve_or_create_by_email_for_tenant(
        db: AsyncSession,
        *,
        email: str,
        organization_id: int,
    ) -> Customer:
        """Tenant-scoped email resolution with race-safe creation.

        Looks up an existing customer by normalized email within the tenant.
        If absent, creates a minimal tenant-owned customer using the email
        local-part as a safe display name. On a concurrent creation race,
        rolls back and re-resolves the winner deterministically.
        """
        normalized = CustomerService._normalize_email(email)
        if normalized is None:
            raise ValueError("Email is required for customer resolution")

        customer = await CustomerRepository.get_by_email_for_tenant(
            db,
            email=normalized,
            organization_id=organization_id,
        )
        if customer is not None:
            return customer

        new_customer = Customer(
            name=CustomerService._email_local_part(normalized),
            email=normalized,
            organization_id=organization_id,
            external_id=None,
        )
        db.add(new_customer)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            # Concurrent creation race: another transaction inserted the
            # customer after our lookup. Re-resolve within the same tenant.
            existing = await CustomerRepository.get_by_email_for_tenant(
                db,
                email=normalized,
                organization_id=organization_id,
            )
            if existing is None:
                # The conflict was not a same-tenant email race; do not
                # attribute it to a customer we cannot see.
                raise
            return existing

        await db.refresh(new_customer)
        return new_customer

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
    async def search_for_tenant(
        db: AsyncSession,
        organization_id: int,
        *,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Customer], int]:
        items = await CustomerRepository.search_for_tenant(
            db,
            organization_id,
            search=search,
            offset=offset,
            limit=limit,
        )
        total = await CustomerRepository.count_search_for_tenant(
            db,
            organization_id,
            search=search,
        )
        return items, total

    @staticmethod
    async def get_for_tenant(
        db: AsyncSession,
        customer_id: int,
        organization_id: int,
    ) -> Customer | None:
        return await CustomerRepository.get_by_id_for_tenant(
            db, customer_id, organization_id
        )
