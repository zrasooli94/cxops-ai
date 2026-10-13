from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rbac import OrganizationRole
from app.models.organization import (
    LIFECYCLE_ONBOARDING,
    LIFECYCLE_PENDING_REVIEW,
    Organization,
)
from app.models.organization_membership import OrganizationMembership
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.organization import OrganizationCreate


class SelfServiceOrganizationLimitError(Exception):
    """Too many unapproved self-service organizations for one identity."""


class OrganizationService:
    @staticmethod
    async def create_with_membership(
        db: AsyncSession,
        data: OrganizationCreate,
        subject: str,
        *,
        email: str | None = None,
    ) -> Organization:
        """Create organization and atomically grant membership to the creator.

        Phase 1Q: a self-service organization starts in ``onboarding``. Its
        owner can set it up and invite a team, but it cannot spend AI allowance,
        enable its widget, or reach external business actions until a platform
        operator approves it.
        """
        # Phase 1Q-R: cap unapproved self-service organizations per identity.
        pending = await db.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .where(
                OrganizationMembership.subject == subject,
                OrganizationMembership.role == OrganizationRole.OWNER.value,
                Organization.lifecycle_status.in_(
                    (LIFECYCLE_ONBOARDING, LIFECYCLE_PENDING_REVIEW)
                ),
            )
        )
        if int(pending or 0) >= get_settings().self_service_organization_limit:
            raise SelfServiceOrganizationLimitError(
                "You already have organizations waiting for approval. "
                "Finish or withdraw those before creating another."
            )
        # ``external_id`` is the operator manifest's tenant slug; it is never
        # client-chosen (the route rejects it), so it is not copied here.
        organization = Organization(
            name=data.name,
            industry=data.industry,
            lifecycle_status=LIFECYCLE_ONBOARDING,
        )
        db.add(organization)
        await db.flush()  # Get organization.id

        membership = OrganizationMembership(
            subject=subject,
            organization_id=organization.id,
            role=OrganizationRole.OWNER,
            email=email,
        )
        db.add(membership)

        await db.commit()
        await db.refresh(organization)
        return organization

    @staticmethod
    async def create(
        db: AsyncSession,
        data: OrganizationCreate,
    ) -> Organization:
        """Legacy create without membership (kept for internal use)."""
        organization = Organization(
            name=data.name,
            industry=data.industry,
            external_id=data.external_id,
        )

        return await OrganizationRepository.create(
            db,
            organization,
        )
