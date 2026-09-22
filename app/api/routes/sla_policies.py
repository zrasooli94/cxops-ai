from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import Capability
from app.models.sla_policy import SLAPolicy
from app.repositories.sla_policy_repository import SLAPolicyRepository
from app.schemas.sla_policy import (
    SLAPolicyCreate,
    SLAPolicyRead,
    SLAPolicyUpdate,
)

router = APIRouter(
    prefix="/sla-policies",
    tags=["SLA Policies"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]
ReadAuthz = Annotated[
    Capability,
    Depends(RequireCapability(Capability.AUTOMATION_READ)),
]
ManageAuthz = Annotated[
    Capability,
    Depends(RequireCapability(Capability.AUTOMATION_MANAGE)),
]


def _handle_integrity_error(exc: IntegrityError) -> HTTPException:
    detail = "SLA policy conflict"
    if exc.orig is not None and hasattr(exc.orig, "diag"):
        constraint = exc.orig.diag.constraint_name
        if constraint and "default" in str(constraint).lower():
            detail = "A default SLA policy already exists for this organization"
        elif constraint and "name" in str(constraint).lower():
            detail = "A SLA policy with this name already exists"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


@router.get(
    "",
    response_model=list[SLAPolicyRead],
)
async def list_sla_policies(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    return await SLAPolicyRepository.list_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )


@router.post(
    "",
    response_model=SLAPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_sla_policy(
    data: SLAPolicyCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ManageAuthz,
):
    policy = SLAPolicy(
        organization_id=tenant.organization_id,
        name=data.name,
        enabled=data.enabled,
        is_default=data.is_default,
        first_response_low_minutes=data.first_response_low_minutes,
        first_response_normal_minutes=data.first_response_normal_minutes,
        first_response_high_minutes=data.first_response_high_minutes,
        first_response_urgent_minutes=data.first_response_urgent_minutes,
        resolution_low_minutes=data.resolution_low_minutes,
        resolution_normal_minutes=data.resolution_normal_minutes,
        resolution_high_minutes=data.resolution_high_minutes,
        resolution_urgent_minutes=data.resolution_urgent_minutes,
    )
    try:
        return await SLAPolicyRepository.create(db, policy)
    except IntegrityError as exc:
        await db.rollback()
        raise _handle_integrity_error(exc)


@router.patch(
    "/{policy_id}",
    response_model=SLAPolicyRead,
)
async def update_sla_policy(
    policy_id: int,
    data: SLAPolicyUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ManageAuthz,
):
    policy = await SLAPolicyRepository.get_by_id_for_tenant(
        db,
        policy_id=policy_id,
        organization_id=tenant.organization_id,
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SLA policy not found",
        )

    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return policy

    # A disabled policy cannot remain the default.
    if changes.get("enabled") is False and policy.is_default:
        changes["is_default"] = False

    try:
        return await SLAPolicyRepository.update_for_tenant(
            db,
            policy=policy,
            changes=changes,
            organization_id=tenant.organization_id,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise _handle_integrity_error(exc)
