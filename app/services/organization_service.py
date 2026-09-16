from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import OrganizationRole
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.organization import OrganizationCreate


class OrganizationService:
    @staticmethod
    async def create_with_membership(
        db: AsyncSession,
        data: OrganizationCreate,
        subject: str,
    ) -> Organization:
        """Create organization and atomically grant membership to the creator."""
        organization = Organization(
            name=data.name,
            industry=data.industry,
            external_id=data.external_id,
        )
        db.add(organization)
        await db.flush()  # Get organization.id

        membership = OrganizationMembership(
            subject=subject,
            organization_id=organization.id,
            role=OrganizationRole.OWNER,
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
