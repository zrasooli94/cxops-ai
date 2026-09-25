"""HTTP routes for Service Transformation scenarios.

These routes are read/write gated by capability; the write routes require
``transformation.simulation.manage`` and read routes
``transformation.simulation.read``, with OWNER/ADMIN holding both via the
role matrix. ``organization_id`` is always resolved from ``CurrentTenant``;
a client-supplied ``organization_id`` in the body is rejected by the schema.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.schemas.service_transformation_simulation import (
    ServiceTransformationScenarioCreate,
    ServiceTransformationScenarioEvaluateResponse,
    ServiceTransformationScenarioRead,
)
from app.services.service_transformation_simulation_service import (
    ScenarioArchivedError,
    ScenarioNotFoundError,
    ServiceTransformationSimulationService,
)

router = APIRouter(
    prefix="/service-operations/simulations",
    tags=["Service Operations"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

SimulationManageAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TRANSFORMATION_SIMULATION_MANAGE)),
]
SimulationReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TRANSFORMATION_SIMULATION_READ)),
]


@router.post(
    "",
    response_model=ServiceTransformationScenarioRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    data: ServiceTransformationScenarioCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: SimulationManageAuthz,
):
    # organization_id comes from CurrentTenant, never from the client payload
    try:
        scenario = await ServiceTransformationSimulationService.create_for_tenant(
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
    return scenario


@router.get(
    "",
    response_model=list[ServiceTransformationScenarioRead],
)
async def list_scenarios(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: SimulationReadAuthz,
):
    return await ServiceTransformationSimulationService.list_for_tenant(
        db=db,
        organization_id=tenant.organization_id,
    )


@router.get(
    "/{scenario_id}",
    response_model=ServiceTransformationScenarioRead,
)
async def get_scenario(
    scenario_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: SimulationReadAuthz,
):
    scenario = await ServiceTransformationSimulationService.get_for_tenant(
        db=db,
        scenario_id=scenario_id,
        organization_id=tenant.organization_id,
    )
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found",
        )
    return scenario


@router.post(
    "/{scenario_id}/evaluate",
    response_model=ServiceTransformationScenarioEvaluateResponse,
)
async def evaluate_scenario(
    scenario_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: SimulationManageAuthz,
):
    try:
        result = await ServiceTransformationSimulationService.evaluate_for_tenant(
            db=db,
            scenario_id=scenario_id,
            organization_id=tenant.organization_id,
        )
    except ScenarioNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found",
        )
    except ScenarioArchivedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    scenario = await ServiceTransformationSimulationService.get_for_tenant(
        db=db,
        scenario_id=scenario_id,
        organization_id=tenant.organization_id,
    )
    assert scenario is not None
    return ServiceTransformationScenarioEvaluateResponse(
        scenario=ServiceTransformationScenarioRead.model_validate(scenario),
        baseline=result["baseline"],
        assumptions=result["assumptions"],
        projected=result["projected"],
        deltas=result["deltas"],
        warnings=result["warnings"],
        measurement_status=result["measurement_status"],
    )


@router.post(
    "/{scenario_id}/archive",
    response_model=ServiceTransformationScenarioRead,
)
async def archive_scenario(
    scenario_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: SimulationManageAuthz,
):
    scenario = await ServiceTransformationSimulationService.archive_for_tenant(
        db=db,
        scenario_id=scenario_id,
        organization_id=tenant.organization_id,
    )
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found",
        )
    return scenario