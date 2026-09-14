from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenant
from app.core.database import get_db
from app.repositories.organization_repository import OrganizationRepository
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
    organization = await OrganizationRepository.get_by_id(
        db, tenant.organization_id
    )

    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return TenantInfo(
        organization_id=tenant.organization_id,
        organization_name=organization.name,
    )