from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant
from app.core.database import get_db
from app.repositories.organization_membership_repository import (
    OrganizationMembershipRepository,
)
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.organization import OrganizationRead
from app.schemas.tenant import TenantInfo

router = APIRouter(
    prefix="/me",
    tags=["Tenant"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.get(
    "/tenant",
    response_model=TenantInfo,
)
async def get_my_tenant(
    tenant: CurrentTenant,
    db: DatabaseSession,
):
    """Return the authenticated principal's resolved organization.

    Safe by construction: echoes only organization identity, never the subject
    or any token/JWT claims. Requires an authenticated principal and an existing
    organization membership.
    """
    organization = await OrganizationRepository.get_by_id(db, tenant.organization_id)

    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return TenantInfo(
        organization_id=tenant.organization_id,
        organization_name=organization.name,
    )


@router.get(
    "/organizations",
    response_model=list[OrganizationRead],
)
async def get_my_organizations(
    principal: CurrentPrincipal,
    db: DatabaseSession,
):
    """Return organizations the authenticated subject is a member of.

    This endpoint intentionally does NOT require a resolved tenant: it is used
    by the frontend organization picker before a tenant has been selected.
    Authorization is still enforced via ``CurrentPrincipal`` and the membership
    table.
    """
    memberships = await OrganizationMembershipRepository.list_for_subject(
        db, principal.subject
    )
    organization_ids = [m.organization_id for m in memberships]
    if not organization_ids:
        return []

    organizations = []
    for org_id in organization_ids:
        org = await OrganizationRepository.get_by_id(db, org_id)
        if org:
            organizations.append(org)
    return organizations
