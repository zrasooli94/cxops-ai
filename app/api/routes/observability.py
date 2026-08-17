from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.observability import (
    AIObservabilitySummary,
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