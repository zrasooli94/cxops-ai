from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
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


@router.post(
    "",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    data: OrganizationCreate,
    db: DatabaseSession,
):
    return await OrganizationService.create(db, data)


@router.get(
    "",
    response_model=list[OrganizationRead],
)
async def list_organizations(
    db: DatabaseSession,
):
    return await OrganizationRepository.list(db)


@router.get(
    "/{organization_id}",
    response_model=OrganizationRead,
)
async def get_organization(
    organization_id: int,
    db: DatabaseSession,
):
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