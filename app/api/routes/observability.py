from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.repositories.service_escalation_repository import (
    ServiceEscalationRepository,
)
from app.schemas.observability import (
    AgentObservabilitySummary,
    AgentOperationalKPIs,
    AgentROISummary,
    AIObservabilityBreakdown,
    AIObservabilitySummary,
)
from app.schemas.service_escalation import EscalationWindowedSummary
from app.services.agent_observability_service import (
    AgentObservabilityService,
)
from app.services.ai_observability_service import (
    AIObservabilityService,
)
from app.services.service_kpi_service import ServiceKPIService

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


@router.get("/service/kpis")
async def service_kpis(
    db: DatabaseSession,
    tenant: CurrentTenant,
    authz: ObservabilityReadAuthz,
    days: int = Query(default=7, ge=1, le=365),
):
    return await ServiceKPIService.kpis_for_tenant(
        db,
        organization_id=tenant.organization_id,
        days=days,
    )


@router.get(
    "/service/escalations",
    response_model=EscalationWindowedSummary,
)
async def service_escalations(
    db: DatabaseSession,
    tenant: CurrentTenant,
    authz: ObservabilityReadAuthz,
    days: int = Query(default=30),
):
    if days not in (7, 30, 90):
        raise HTTPException(
            status_code=400,
            detail="days must be one of 7, 30, 90",
        )
    return await ServiceEscalationRepository.windowed_summary_for_tenant(
        db,
        organization_id=tenant.organization_id,
        now=datetime.now(UTC),
        days=days,
    )
