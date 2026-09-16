from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.schemas.observability import (
    AgentObservabilitySummary,
    AgentOperationalKPIs,
    AgentROISummary,
    AIObservabilityBreakdown,
    AIObservabilitySummary,
)
from app.services.agent_observability_service import (
    AgentObservabilityService,
)
from app.services.ai_observability_service import (
    AIObservabilityService,
)

router = APIRouter(
    prefix="/observability",
    tags=["Observability"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

ObservabilityReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.OBSERVABILITY_READ)),
]


@router.get(
    "/ai/summary",
    response_model=AIObservabilitySummary,
)
async def ai_summary(
    db: DatabaseSession,
    tenant: CurrentTenant,
    authz: ObservabilityReadAuthz,
):
    return await AIObservabilityService.summary(
        db,
        tenant.organization_id,
    )


@router.get(
    "/agent/summary",
    response_model=AgentObservabilitySummary,
)
async def agent_summary(
    db: DatabaseSession,
    tenant: CurrentTenant,
    authz: ObservabilityReadAuthz,
):
    return await AgentObservabilityService.summary(
        db,
        tenant.organization_id,
    )


@router.get(
    "/ai/by-feature",
    response_model=AIObservabilityBreakdown,
)
async def ai_by_feature(
    db: DatabaseSession,
    tenant: CurrentTenant,
    authz: ObservabilityReadAuthz,
):
    return await AIObservabilityService.breakdown(
        db,
        tenant.organization_id,
    )


@router.get(
    "/agent/kpis",
    response_model=AgentOperationalKPIs,
)
async def agent_operational_kpis(
    db: DatabaseSession,
    tenant: CurrentTenant,
    authz: ObservabilityReadAuthz,
):
    return await AgentObservabilityService.operational_kpis(
        db,
        tenant.organization_id,
    )


@router.get(
    "/agent/roi",
    response_model=AgentROISummary,
)
async def agent_roi(
    db: DatabaseSession,
    tenant: CurrentTenant,
    authz: ObservabilityReadAuthz,
):
    return await AgentObservabilityService.roi_summary(
        db,
        tenant.organization_id,
    )
