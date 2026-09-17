from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
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

AgentRunAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.AGENT_RUN)),
]
AgentApproveAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.AGENT_APPROVE)),
]
AgentExecuteAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.AGENT_EXECUTE)),
]


def serialize_run(
    run,
) -> dict:

    return {
        "run_id": run.run_id,
        "ticket_id": run.ticket_id,
        "organization_id": run.organization_id,
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
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: AgentRunAuthz,
):

    try:
        return await agent_workflow_service.analyze(
            db=db,
            ticket_id=ticket_id,
            organization_id=tenant.organization_id,
            authz=authz,
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
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: AgentApproveAuthz,
):

    try:
        run = await AgentApprovalService.approve(
            db=db,
            run_id=run_id,
            organization_id=tenant.organization_id,
            note=data.note,
            authz=authz,
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
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: AgentRunAuthz,
    run_status: str | None = None,
    limit: int = 100,
):
    safe_limit = max(
        1,
        min(limit, 200),
    )

    runs = await AgentRunRepository.list_runs_for_tenant(
        db,
        organization_id=tenant.organization_id,
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
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: AgentApproveAuthz,
):

    try:
        run = await AgentApprovalService.reject(
            db=db,
            run_id=run_id,
            organization_id=tenant.organization_id,
            note=data.note,
            authz=authz,
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
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: AgentExecuteAuthz,
):

    run = await AgentRunRepository.get_by_run_id_for_tenant(
        db=db,
        run_id=run_id,
        organization_id=tenant.organization_id,
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

    # --- Phase 1D.3: Manual execute two-key authorization ---
    # Verify agent.execute + all tool required capabilities.
    # Distinguish between approval-required and low-risk plans.
    plan = run.tool_plan or []
    has_approval_required = any(
        tool.get("requires_approval", True)
        for tool in plan
    )

    if has_approval_required:
        # Medium/high plan: verify existing human_approval authorization
        # is intact; do NOT overwrite provenance with executor identity.
        if run.authorization_source != "human_approval":
            raise HTTPException(
                status_code=(status.HTTP_403_FORBIDDEN),
                detail=("Agent run requires human approval but has no "
                        "valid human_approval authorization."),
            )
        # Verify digest using centralized ToolAuthorizationService helper
        # (the digest was persisted during approval/execution preflight)
        if run.authorization_digest:
            from app.services.tool_authorization_service import (
                ToolAuthorizationService,
            )
            if not ToolAuthorizationService.validate_run_digest(
                run,
            ):
                raise HTTPException(
                    status_code=(status.HTTP_403_FORBIDDEN),
                    detail=("Intent digest mismatch — plan was modified "
                            "after authorization."),
                )
        # Execution caller still independently needs agent.execute
        # + required tool capabilities; theenqueue gate enforces this.
    else:
        # Low-risk plan with no prior human approval:
        # Require agent.execute + all tool required capabilities.
        # Before queue creation, persist human_execute authorization.
        from app.services.tool_authorization_service import (
            ToolAuthorizationService,
        )
        # Verify agent.execute and all required_capability values
        for tool in plan:
            req_cap = tool.get("required_capability")
            if req_cap is not None and not authz.has(req_cap):
                    raise HTTPException(
                        status_code=(status.HTTP_403_FORBIDDEN),
                        detail=(f"Capability {req_cap.value} required for "
                                f"tool {tool.get('tool')} but not held by "
                                f"the executing authorizer."),
                    )
            if tool.get("risk_level") != "low":
                raise HTTPException(
                    status_code=(status.HTTP_403_FORBIDDEN),
                    detail=("Low-risk plan verification failed: tool "
                            f"{tool.get('tool')} risk_level is not low."),
                    )
        # Persist human_execute authorization metadata before queue creation
        from app.services.tool_authorization_service import (
            ToolAuthorizationService,
        )
        run.authorization_source = "human_execute"
        run.authorized_by_subject = authz.subject
        run.authorized_at = datetime.now(UTC)
        run.tool_policy_version = ToolAuthorizationService.TOOL_POLICY_VERSION
        run.authorization_digest = ToolAuthorizationService.compute_run_digest(
            run_id=run.run_id,
            organization_id=run.organization_id,
            ticket_id=run.ticket_id,
            tool_plan=plan,
        )

    try:
        return await IntegrationJobService.enqueue_agent_execution(
            db=db,
            run_id=run_id,
            organization_id=tenant.organization_id,
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
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: AgentRunAuthz,
):
    run = await AgentRunRepository.get_by_run_id_for_tenant(
        db,
        run_id,
        tenant.organization_id,
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