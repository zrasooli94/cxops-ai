from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.agent import (
    AgentAnalysisResponse,
)
from app.services.agent_workflow_service import (
    TicketNotFoundError,
    agent_workflow_service,
)


router = APIRouter(
    prefix="/agent",
    tags=["Agentic AI"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.post(
    "/tickets/{ticket_id}/analyze",
    response_model=AgentAnalysisResponse,
)
async def analyze_ticket(
    ticket_id: int,
    db: DatabaseSession,
):

    try:

        return await (
            agent_workflow_service
            .analyze(
                db=db,
                ticket_id=ticket_id,
            )
        )

    except TicketNotFoundError as exc:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=str(exc),
        ) from exc