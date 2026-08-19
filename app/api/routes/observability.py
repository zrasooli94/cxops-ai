from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

from app.schemas.observability import (
    AgentObservabilitySummary,
    AIObservabilitySummary,
    AIObservabilityBreakdown,
    AgentOperationalKPIs,
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


@router.get(
    "/ai/summary",
    response_model=AIObservabilitySummary,
)
async def ai_summary(
    db: DatabaseSession,
):
    return await AIObservabilityService.summary(
        db
    )


@router.get(
    "/agent/summary",
    response_model=AgentObservabilitySummary,
)
async def agent_summary(
    db: DatabaseSession,
):
    return await AgentObservabilityService.summary(
        db
    )

@router.get(
    "/ai/by-feature",
    response_model=AIObservabilityBreakdown,
)
async def ai_by_feature(
    db: DatabaseSession,
):
    return await (
        AIObservabilityService
        .breakdown(
            db
        )
    )

@router.get(
    "/agent/kpis",
    response_model=AgentOperationalKPIs,
)
async def agent_operational_kpis(
    db: DatabaseSession,
):
    return await (
        AgentObservabilityService
        .operational_kpis(
            db
        )
    )