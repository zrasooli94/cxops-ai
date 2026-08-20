from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)
from app.schemas.agent import (
    AgentAnalysisResponse,
    AgentExecutionQueuedResponse,
    AgentReviewRequest,
    AgentRunResponse,
)
from app.services.agent_approval_service import (
    AgentApprovalService,
    AgentRunNotFoundError,
    InvalidAgentRunStateError,
)
from app.services.agent_workflow_service import (
    TicketNotFoundError,
    agent_workflow_service,
)
from app.services.integration_job_service import (
    AgentExecutionQueueBlockedError,
    IntegrationJobService,
)

router = APIRouter(
    prefix="/agent",
    tags=["Agentic AI"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


def serialize_run(
    run,
) -> dict:

    return {
        "run_id": run.run_id,
        "ticket_id": run.ticket_id,
        "action": run.action,
        "status": run.status,
        "reason": run.reason,
        "recommended_team": (run.recommended_team),
        "recommended_priority": (run.recommended_priority),
        "response_draft": (run.response_draft),
        "requires_human_approval": (run.requires_human_approval),
        "reviewer_note": (run.reviewer_note),
        "workflow_path": (run.workflow_path or []),
        "tool_plan": (run.tool_plan or []),
    }


@router.post(
    "/tickets/{ticket_id}/analyze",
    response_model=AgentAnalysisResponse,
)
async def analyze_ticket(
    ticket_id: int,
    db: DatabaseSession,
):

    try:
        return await agent_workflow_service.analyze(
            db=db,
            ticket_id=ticket_id,
        )

    except TicketNotFoundError as exc:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


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
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc

    except InvalidAgentRunStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=str(exc),
        ) from exc


@router.get(
    "/runs",
    response_model=list[AgentRunResponse],
)
async def list_agent_runs(
    db: DatabaseSession,
    run_status: str | None = None,
    limit: int = 100,
):
    safe_limit = max(
        1,
        min(limit, 200),
    )

    runs = await AgentRunRepository.list_runs(
        db,
        run_status=run_status,
        limit=safe_limit,
    )

    return [serialize_run(run) for run in runs]


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
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc

    except InvalidAgentRunStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=str(exc),
        ) from exc


@router.post(
    "/runs/{run_id}/execute",
    response_model=(AgentExecutionQueuedResponse),
    status_code=(status.HTTP_202_ACCEPTED),
)
async def execute_agent_run(
    run_id: str,
    db: DatabaseSession,
):

    run = await AgentRunRepository.get_by_run_id(
        db=db,
        run_id=run_id,
    )

    if run is None:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=(f"Agent run {run_id} was not found."),
        )

    if run.status == "executed":
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=("Agent run has already been executed."),
        )

    if run.action in {
        "human_review",
        "no_action",
    }:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=(f"Action '{run.action}' does not require external execution."),
        )

    if run.status not in {
        "approved",
        "execution_failed",
    }:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=(f"Agent run cannot be queued from status {run.status}."),
        )

    try:
        return await IntegrationJobService.enqueue_agent_execution(
            db=db,
            run_id=run_id,
        )

    except AgentExecutionQueueBlockedError as exc:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=str(exc),
        ) from exc


@router.get(
    "/runs/{run_id}/events",
)
async def list_agent_run_events(
    run_id: str,
    db: DatabaseSession,
):
    run = await AgentRunRepository.get_by_run_id(
        db,
        run_id,
    )

    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Agent run {run_id} was not found"),
        )

    events = await AgentRunRepository.list_events(
        db,
        agent_run_id=run.id,
    )

    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "actor": event.actor,
            "note": event.note,
            "event_data": event.event_data,
        }
        for event in events
    ]
