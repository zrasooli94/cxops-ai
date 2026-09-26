"""HTTP routes for Service Transformation experiments.

Routes are capability-gated: write/transition routes require
``transformation.experiment.manage`` and read routes
``transformation.experiment.read`` (OWNER/ADMIN/SUPERVISOR hold both via the
role matrix; AGENT and VIEWER hold neither). ``organization_id`` is always
resolved from ``CurrentTenant``; a client-supplied ``organization_id`` in the
body is rejected by the schema. State-transition failures are reported as 409
conflicts with a precise detail; tenant-missing rows are always a generic 404.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.schemas.service_transformation_experiment import (
    ServiceTransformationExperimentCreate,
    ServiceTransformationExperimentRead,
)
from app.services.service_transformation_experiment_service import (
    ExperimentNotFoundError,
    ExperimentStateError,
    ServiceTransformationExperimentService,
)

router = APIRouter(
    prefix="/service-operations/experiments",
    tags=["Service Operations"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

ExperimentManageAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TRANSFORMATION_EXPERIMENT_MANAGE)),
]
ExperimentReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TRANSFORMATION_EXPERIMENT_READ)),
]


@router.post(
    "",
    response_model=ServiceTransformationExperimentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment(
    data: ServiceTransformationExperimentCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentManageAuthz,
):
    try:
        experiment = await ServiceTransformationExperimentService.create_for_tenant(
            db=db,
            data=data,
            organization_id=tenant.organization_id,
            created_by_subject=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return experiment


@router.get(
    "",
    response_model=list[ServiceTransformationExperimentRead],
)
async def list_experiments(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentReadAuthz,
):
    return await ServiceTransformationExperimentService.list_for_tenant(
        db=db,
        organization_id=tenant.organization_id,
    )


@router.get(
    "/{experiment_id}",
    response_model=ServiceTransformationExperimentRead,
)
async def get_experiment(
    experiment_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentReadAuthz,
):
    experiment = await ServiceTransformationExperimentService.get_for_tenant(
        db=db,
        experiment_id=experiment_id,
        organization_id=tenant.organization_id,
    )
    if experiment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found",
        )
    return experiment


async def _run_transition(handler, db, experiment_id, tenant):
    try:
        return await handler(
            db=db,
            experiment_id=experiment_id,
            organization_id=tenant.organization_id,
        )
    except ExperimentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found",
        )
    except ExperimentStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )


@router.post(
    "/{experiment_id}/capture-baseline",
    response_model=ServiceTransformationExperimentRead,
)
async def capture_experiment_baseline(
    experiment_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentManageAuthz,
):
    return await _run_transition(
        ServiceTransformationExperimentService.capture_baseline_for_tenant,
        db,
        experiment_id,
        tenant,
    )


@router.post(
    "/{experiment_id}/start",
    response_model=ServiceTransformationExperimentRead,
)
async def start_experiment(
    experiment_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentManageAuthz,
):
    return await _run_transition(
        ServiceTransformationExperimentService.start_for_tenant,
        db,
        experiment_id,
        tenant,
    )


@router.post(
    "/{experiment_id}/complete",
    response_model=ServiceTransformationExperimentRead,
)
async def complete_experiment(
    experiment_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentManageAuthz,
):
    return await _run_transition(
        ServiceTransformationExperimentService.complete_for_tenant,
        db,
        experiment_id,
        tenant,
    )


@router.post(
    "/{experiment_id}/cancel",
    response_model=ServiceTransformationExperimentRead,
)
async def cancel_experiment(
    experiment_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentManageAuthz,
):
    return await _run_transition(
        ServiceTransformationExperimentService.cancel_for_tenant,
        db,
        experiment_id,
        tenant,
    )


@router.post(
    "/{experiment_id}/archive",
    response_model=ServiceTransformationExperimentRead,
)
async def archive_experiment(
    experiment_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ExperimentManageAuthz,
):
    return await _run_transition(
        ServiceTransformationExperimentService.archive_for_tenant,
        db,
        experiment_id,
        tenant,
    )