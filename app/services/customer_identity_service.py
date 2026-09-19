from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.customer_identity import CustomerIdentity
from app.repositories.customer_identity_repository import (
    CustomerIdentityRepository,
)
from app.repositories.customer_repository import CustomerRepository


class CustomerIdentityConflictError(Exception):
    """Raised when provider identity evidence contradicts existing tenant data.

    The message carries no provider identifiers, email addresses, phone numbers,
    or other customer PII.
    """


class CustomerIdentityService:
    """Domain service for deterministic provider identity normalization,
    tenant-scoped lookup, conflict-safe linking, and race-safe creation.
    """

    @staticmethod
    def _normalize_identifier(
        *,
        provider: str,
        identity_type: str,
        identifier: str | None,
    ) -> str | None:
        if identifier is None:
            return None

        if identity_type == "email":
            normalized = identifier.strip().lower()
            return normalized if normalized else None

        if provider == "zendesk" and identity_type == "user_id":
            normalized = identifier.strip()
            if not normalized:
                return None
            # Canonical decimal representation: strip leading zeros and
            # re-stringify so "006119" and "6119" resolve to the same id.
            if normalized.lstrip("0") == "":
                return "0"
            return str(int(normalized))

        # phone / whatsapp / chat / generic: trim whitespace, preserve case
        normalized = identifier.strip()
        return normalized if normalized else None

    @classmethod
    def normalize(
        cls,
        *,
        provider: str,
        identity_type: str,
        identifier: str | None,
    ) -> str | None:
        return cls._normalize_identifier(
            provider=provider,
            identity_type=identity_type,
            identifier=identifier,
        )

    @classmethod
    async def resolve_by_provider_identity(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        provider: str,
        identity_type: str,
        identifier: str,
    ) -> CustomerIdentity | None:
        normalized = cls._normalize_identifier(
            provider=provider,
            identity_type=identity_type,
            identifier=identifier,
        )
        if normalized is None:
            return None

        return await CustomerIdentityRepository.get_by_provider_identity_for_tenant(
            db,
            organization_id=organization_id,
            provider=provider,
            identity_type=identity_type,
            normalized_identifier=normalized,
        )

    @classmethod
    async def link_identity(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        customer_id: int,
        provider: str,
        identity_type: str,
        identifier: str,
    ) -> CustomerIdentity:
        """Create a provider identity for an existing customer.

        Fails closed with ``CustomerIdentityConflictError`` if the same
        provider identity is already attached to a different customer in the
        tenant. Concurrent races are resolved through the database unique
        constraint and re-lookup.
        """
        normalized = cls._normalize_identifier(
            provider=provider,
            identity_type=identity_type,
            identifier=identifier,
        )
        if normalized is None:
            raise CustomerIdentityConflictError(
                "Provider identity identifier is empty or invalid."
            )

        existing = (
            await CustomerIdentityRepository.get_by_provider_identity_for_tenant(
                db,
                organization_id=organization_id,
                provider=provider,
                identity_type=identity_type,
                normalized_identifier=normalized,
            )
        )
        if existing is not None:
            if existing.customer_id == customer_id:
                return existing
            raise CustomerIdentityConflictError(
                "Provider identity already belongs to a different customer."
            )

        identity = CustomerIdentity(
            organization_id=organization_id,
            customer_id=customer_id,
            provider=provider,
            identity_type=identity_type,
            identifier=identifier.strip(),
            normalized_identifier=normalized,
        )

        try:
            return await CustomerIdentityRepository.create(db, identity)
        except IntegrityError:
            await db.rollback()
            existing = await CustomerIdentityRepository.get_by_provider_identity_for_tenant(
                db,
                organization_id=organization_id,
                provider=provider,
                identity_type=identity_type,
                normalized_identifier=normalized,
            )
            if existing is not None:
                if existing.customer_id == customer_id:
                    return existing
                raise CustomerIdentityConflictError(
                    "Provider identity already belongs to a different customer."
                ) from None
            # The conflict was not on the identity unique boundary; surface it.
            raise

    @classmethod
    async def resolve_or_create_customer_by_provider_identity(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        provider: str,
        identity_type: str,
        identifier: str,
        display_name: str | None = None,
        email: str | None = None,
    ) -> tuple[Customer, CustomerIdentity]:
        """Resolve a customer by provider identity, creating both customer and
        identity when absent.

        If ``email`` is provided, an existing customer with that email in the
        tenant is reused and the provider identity is attached. If the email
        belongs to a different customer than an existing provider identity,
        a conflict is raised instead of moving the identity or merging.
        """
        normalized = cls._normalize_identifier(
            provider=provider,
            identity_type=identity_type,
            identifier=identifier,
        )
        if normalized is None:
            raise CustomerIdentityConflictError(
                "Provider identity identifier is empty or invalid."
            )

        identity = await CustomerIdentityRepository.get_by_provider_identity_for_tenant(
            db,
            organization_id=organization_id,
            provider=provider,
            identity_type=identity_type,
            normalized_identifier=normalized,
        )

        if identity is not None:
            customer = identity.customer
            if customer is None:
                customer = await CustomerRepository.get_by_id_for_tenant(
                    db,
                    identity.customer_id,
                    organization_id,
                )
            if customer is None:
                raise CustomerIdentityConflictError(
                    "Provider identity maps to a customer that does not exist."
                )
            return customer, identity

        email_customer = None
        if email is not None:
            normalized_email = cls._normalize_identifier(
                provider="email",
                identity_type="email",
                identifier=email,
            )
            if normalized_email is not None:
                email_customer = await CustomerRepository.get_by_email_for_tenant(
                    db,
                    email=normalized_email,
                    organization_id=organization_id,
                )

        if email_customer is not None:
            customer = email_customer
        else:
            customer_name = display_name or f"{provider.title()} Customer"
            customer = Customer(
                name=customer_name,
                email=email or f"{provider}-{normalized}@placeholder.local",
                organization_id=organization_id,
                external_id=None,
            )
            db.add(customer)
            try:
                await db.commit()
                await db.refresh(customer)
            except IntegrityError:
                await db.rollback()
                if email is not None:
                    existing_by_email = await CustomerRepository.get_by_email_for_tenant(
                        db,
                        email=cls._normalize_identifier(
                            provider="email",
                            identity_type="email",
                            identifier=email,
                        ) or "",
                        organization_id=organization_id,
                    )
                    if existing_by_email is not None:
                        customer = existing_by_email
                    else:
                        raise
                else:
                    raise

        new_identity = CustomerIdentity(
            organization_id=organization_id,
            customer_id=customer.id,
            provider=provider,
            identity_type=identity_type,
            identifier=identifier.strip(),
            normalized_identifier=normalized,
        )
        db.add(new_identity)
        try:
            await db.commit()
            await db.refresh(new_identity)
        except IntegrityError:
            await db.rollback()
            existing = await CustomerIdentityRepository.get_by_provider_identity_for_tenant(
                db,
                organization_id=organization_id,
                provider=provider,
                identity_type=identity_type,
                normalized_identifier=normalized,
            )
            if existing is not None:
                existing_customer = await CustomerRepository.get_by_id_for_tenant(
                    db,
                    existing.customer_id,
                    organization_id,
                )
                if existing_customer is None:
                    raise CustomerIdentityConflictError(
                        "Provider identity maps to a customer that does not exist."
                    ) from None
                return existing_customer, existing
            raise

        return customer, new_identity

    @classmethod
    async def safe_update_customer_email(
        cls,
        db: AsyncSession,
        *,
        customer: Customer,
        new_email: str | None,
        organization_id: int,
    ) -> Customer:
        """Update a customer's email only when the change does not collide.

        - new email unused in tenant -> update
        - new email already belongs to this customer -> no-op
        - new email belongs to another customer -> conflict
        - new email None -> update to placeholder (Zendesk removed email)
        """
        if new_email is None:
            if "@placeholder.local" not in (customer.email or ""):
                customer.email = f"customer-{customer.id}@placeholder.local"
            await db.commit()
            await db.refresh(customer)
            return customer

        normalized_email = cls._normalize_identifier(
            provider="email",
            identity_type="email",
            identifier=new_email,
        )
        if normalized_email is None:
            raise CustomerIdentityConflictError("Customer email is invalid.")

        if customer.email == normalized_email:
            return customer

        existing = await CustomerRepository.get_by_email_for_tenant(
            db,
            email=normalized_email,
            organization_id=organization_id,
        )
        if existing is not None and existing.id != customer.id:
            raise CustomerIdentityConflictError(
                "Email belongs to a different customer in this organization."
            )

        customer.email = normalized_email
        await db.commit()
        await db.refresh(customer)
        return customer
