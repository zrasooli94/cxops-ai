from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from app.schemas.agent import (
    AgentAnalysisResponse,
    AgentExecutionResponse,
    AgentReviewRequest,
    AgentRunResponse,
)
from app.services.agent_approval_service import (
    AgentApprovalService,
    AgentRunNotFoundError,
    InvalidAgentRunStateError,
)
from app.services.agent_execution_service import (
    AgentExecutionError,
    AgentExecutionStateError,
    agent_execution_service,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

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

def serialize_run(run):
    return {
        "run_id": run.run_id,
        "ticket_id": run.ticket_id,
        "action": run.action,
        "status": run.status,
        "reason": run.reason,
        "recommended_team": (
            run.recommended_team
        ),
        "recommended_priority": (
            run.recommended_priority
        ),
        "response_draft": (
            run.response_draft
        ),
        "requires_human_approval": (
            run.requires_human_approval
        ),
        "reviewer_note": (
            run.reviewer_note
        ),
    }

@router.post(
    "/runs/{run_id}/approve",
    response_model=AgentRunResponse,
)
async def approve_agent_run(
    run_id: str,
    data: AgentReviewRequest,
    db: DatabaseSession,
):

    try:
        run = await AgentApprovalService.approve(
            db=db,
            run_id=run_id,
            note=data.note,
        )

        return serialize_run(run)

    except AgentRunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except InvalidAgentRunStateError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@router.post(
    "/runs/{run_id}/reject",
    response_model=AgentRunResponse,
)
async def reject_agent_run(
    run_id: str,
    data: AgentReviewRequest,
    db: DatabaseSession,
):

    try:
        run = await AgentApprovalService.reject(
            db=db,
            run_id=run_id,
            note=data.note,
        )

        return serialize_run(run)

    except AgentRunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except InvalidAgentRunStateError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

@router.post(
    "/runs/{run_id}/execute",
    response_model=AgentExecutionResponse,
)
async def execute_agent_run(
    run_id: str,
    db: DatabaseSession,
):

    try:

        return await (
            agent_execution_service
            .execute(
                db=db,
                run_id=run_id,
            )
        )

    except AgentExecutionStateError as exc:

        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except AgentExecutionError as exc:

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc