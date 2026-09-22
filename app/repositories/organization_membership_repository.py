from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import OrganizationRole
from app.models.organization_membership import OrganizationMembership


class OrganizationMembershipRepository:
    """Repository for organization membership lookups.

    Every membership query that involves an explicitly selected organization
    must bind BOTH ``subject`` AND ``organization_id`` so a caller can never
    resolve a tenant they were not granted.
    """

    @staticmethod
    async def list_for_subject(
        db: AsyncSession,
        subject: str,
    ) -> list[OrganizationMembership]:
        """All memberships for a subject, in deterministic order."""
        result = await db.execute(
            select(OrganizationMembership)
            .where(OrganizationMembership.subject == subject)
            .order_by(OrganizationMembership.organization_id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_for_subject_and_organization(
        db: AsyncSession,
        subject: str,
        organization_id: int,
    ) -> OrganizationMembership | None:
        """Look up one membership, always correlated against the subject."""
        result = await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.subject == subject,
                OrganizationMembership.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_organization_and_subject(
        db: AsyncSession,
        *,
        organization_id: int,
        subject: str,
    ) -> OrganizationMembership | None:
        result = await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.subject == subject,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        subject: str,
        organization_id: int,
        role: OrganizationRole,
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            subject=subject,
            organization_id=organization_id,
            role=role,
        )
        db.add(membership)
        return membership
