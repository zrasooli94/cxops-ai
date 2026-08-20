from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.organization import OrganizationCreate


class OrganizationService:
    @staticmethod
    async def create(
        db: AsyncSession,
        data: OrganizationCreate,
    ) -> Organization:
        organization = Organization(
            name=data.name,
            industry=data.industry,
            external_id=data.external_id,
        )

        return await OrganizationRepository.create(
            db,
            organization,
        )
