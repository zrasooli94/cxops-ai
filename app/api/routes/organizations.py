from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.repositories.organization_membership_repository import (
    OrganizationMembershipRepository,
)
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.organization import OrganizationCreate, OrganizationRead
from app.services.organization_service import OrganizationService

router = APIRouter(
    prefix="/organizations",
    tags=["Organizations"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

OrganizationReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.ORGANIZATION_READ)),
]


@router.post(
    "",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    data: OrganizationCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
):
    # Atomic creation: organization + creator membership in one transaction.
    # This is tenant bootstrap, NOT RBAC (no roles assigned).
    return await OrganizationService.create_with_membership(db, data, principal.subject)


@router.get(
    "",
    response_model=list[OrganizationRead],
)
async def list_organizations(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: OrganizationReadAuthz,
):
    # Return only organizations the authenticated subject has memberships for
    memberships = await OrganizationMembershipRepository.list_for_subject(
        db, principal.subject
    )
    organization_ids = [m.organization_id for m in memberships]
    if not organization_ids:
        return []

    # Fetch details for each
    organizations = []
    for org_id in organization_ids:
        org = await OrganizationRepository.get_by_id(db, org_id)
        if org:
            organizations.append(org)
    return organizations


@router.get(
    "/{organization_id}",
    response_model=OrganizationRead,
)
async def get_organization(
    organization_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: OrganizationReadAuthz,
):
    # Validate membership before returning
    membership = (
        await OrganizationMembershipRepository.get_for_subject_and_organization(
            db, principal.subject, organization_id
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    organization = await OrganizationRepository.get_by_id(
        db,
        organization_id,
    )

    if organization is None:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    return organization
