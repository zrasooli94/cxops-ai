from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import Capability
from app.models.service_queue import ServiceQueue
from app.repositories.service_queue_repository import ServiceQueueRepository
from app.repositories.sla_policy_repository import SLAPolicyRepository
from app.schemas.service_queue import (
    ServiceQueueCreate,
    ServiceQueueListItem,
    ServiceQueueRead,
    ServiceQueueUpdate,
)

router = APIRouter(
    prefix="/service-queues",
    tags=["Service Queues"],
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
    detail = "Service queue conflict"
    if exc.orig is not None and hasattr(exc.orig, "diag"):
        constraint = exc.orig.diag.constraint_name
        if constraint and "default" in str(constraint).lower():
            detail = "A default service queue already exists for this organization"
        elif constraint and "key" in str(constraint).lower():
            detail = "A service queue with this key already exists"
        elif constraint and "name" in str(constraint).lower():
            detail = "A service queue with this name already exists"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


@router.get(
    "",
    response_model=list[ServiceQueueRead],
)
async def list_service_queues(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    return await ServiceQueueRepository.list_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )


@router.get(
    "/active",
    response_model=list[ServiceQueueListItem],
)
async def list_active_service_queues(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    return await ServiceQueueRepository.list_active_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )


@router.post(
    "",
    response_model=ServiceQueueRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_service_queue(
    data: ServiceQueueCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ManageAuthz,
):
    if data.sla_policy_id is not None:
        policy = await SLAPolicyRepository.get_by_id_for_tenant(
            db,
            policy_id=data.sla_policy_id,
            organization_id=tenant.organization_id,
        )
        if policy is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="SLA policy not found",
            )

    queue = ServiceQueue(
        organization_id=tenant.organization_id,
        key=data.key,
        name=data.name,
        description=data.description,
        active=data.active,
        is_default=data.is_default,
        sla_policy_id=data.sla_policy_id,
    )
    try:
        return await ServiceQueueRepository.create(db, queue)
    except IntegrityError as exc:
        await db.rollback()
        raise _handle_integrity_error(exc)


@router.get(
    "/{queue_id}",
    response_model=ServiceQueueRead,
)
async def get_service_queue(
    queue_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    queue = await ServiceQueueRepository.get_by_id_for_tenant(
        db,
        queue_id=queue_id,
        organization_id=tenant.organization_id,
    )
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service queue not found",
        )
    return queue


@router.patch(
    "/{queue_id}",
    response_model=ServiceQueueRead,
)
async def update_service_queue(
    queue_id: int,
    data: ServiceQueueUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ManageAuthz,
):
    queue = await ServiceQueueRepository.get_by_id_for_tenant(
        db,
        queue_id=queue_id,
        organization_id=tenant.organization_id,
    )
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service queue not found",
        )

    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return queue

    if "sla_policy_id" in changes and changes["sla_policy_id"] is not None:
        policy = await SLAPolicyRepository.get_by_id_for_tenant(
            db,
            policy_id=changes["sla_policy_id"],
            organization_id=tenant.organization_id,
        )
        if policy is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="SLA policy not found",
            )

    # An inactive queue cannot remain the default.
    if changes.get("active") is False and queue.is_default:
        changes["is_default"] = False

    try:
        return await ServiceQueueRepository.update_for_tenant(
            db,
            queue=queue,
            changes=changes,
            organization_id=tenant.organization_id,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise _handle_integrity_error(exc)
