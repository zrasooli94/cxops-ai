from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import OrganizationRole
from app.models.organization import LIFECYCLE_ONBOARDING, Organization
from app.models.organization_membership import OrganizationMembership
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.organization import OrganizationCreate


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
        organization = Organization(
            name=data.name,
            industry=data.industry,
            external_id=data.external_id,
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
