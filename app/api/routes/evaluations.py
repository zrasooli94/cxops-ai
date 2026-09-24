"""Tenant-scoped API for invoking and reading AI evaluation runs.

Every route derives the organization from ``CurrentTenant`` only and never from
request bodies. Reads require ``evaluation.read``; starting a run requires
``evaluation.manage``. Starting a run only provisions the run and enqueues a
durable job — evaluators never run inside the HTTP request.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.schemas.evaluation import (
    EvalAgentCaseInputs,
    EvalRAGCaseInputs,
    EvaluationBaselineListResponse,
    EvaluationBaselineRead,
    EvaluationCaseListResponse,
    EvaluationReleaseDecisionCreate,
    EvaluationReleaseDecisionListResponse,
    EvaluationReleaseDecisionRead,
    EvaluationRunComparison,
    EvaluationRunListResponse,
    EvaluationRunQueuedResponse,
    EvaluationRunRead,
    TargetType,
)
from app.services.ai_evaluation_service import (
    _APPROVAL_BLOCKED_ERROR,
    AIEvaluationService,
)
from app.services.integration_job_service import IntegrationJobService

router = APIRouter(
    prefix="/evaluations",
    tags=["AI Evaluations"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

EvaluationReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.EVALUATION_READ)),
]
EvaluationManageAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.EVALUATION_MANAGE)),
]


def _serialize_run(run) -> dict:
    """Explicit run projection that never includes the ``input`` snapshot."""
    return {
        "id": run.id,
        "run_id": run.run_id,
        "organization_id": run.organization_id,
        "target_type": run.target_type,
        "status": run.status,
        "model": run.model,
        "embedding_model": run.embedding_model,
        "agent_decision_version": run.agent_decision_version,
        "tool_policy_version": run.tool_policy_version,
        "corpus_revision": run.corpus_revision,
        "trigger_source": run.trigger_source,
        "requested_by_subject": run.requested_by_subject,
        "pass_rate": run.pass_rate,
        "metrics": run.metrics or {},
        "error": run.error,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def _serialize_case(case) -> dict:
    return {
        "id": case.id,
        "run_id": case.run_id,
        "organization_id": case.organization_id,
        "case_id": case.case_id,
        "case_type": case.case_type,
        "input": case.input or {},
        "expected": case.expected or {},
        "actual": case.actual or {},
        "dimensions": case.dimensions or {},
        "latency_ms": case.latency_ms,
        "total_tokens": case.total_tokens,
        "estimated_cost_usd": case.estimated_cost_usd,
        "fingerprint": case.fingerprint,
        "created_at": case.created_at,
    }


def _serialize_baseline(baseline) -> dict:
    """Explicit baseline projection (matches EvaluationBaselineRead)."""
    return {
        "id": baseline.id,
        "organization_id": baseline.organization_id,
        "target_type": baseline.target_type,
        "version": baseline.version,
        "model": baseline.model,
        "embedding_model": baseline.embedding_model,
        "agent_decision_version": baseline.agent_decision_version,
        "tool_policy_version": baseline.tool_policy_version,
        "corpus_revision": baseline.corpus_revision,
        "pass_rate": baseline.pass_rate,
        "metrics": baseline.metrics or {},
        "cases_count": baseline.cases_count,
        "created_by_subject": baseline.created_by_subject,
        "promoted": baseline.promoted,
        "created_at": baseline.created_at,
    }


def _serialize_release_decision(record) -> dict:
    """Explicit release-decision projection (matches EvaluationReleaseDecisionRead).

    Only the safe stored fields are exposed: decision, decider, bounded note,
    and the derived comparison snapshot. The candidate run's `input` snapshot
    and any customer/secrets data are never part of this projection.
    """
    return {
        "id": record.id,
        "organization_id": record.organization_id,
        "candidate_run_id": record.candidate_run_id,
        "baseline_id": record.baseline_id,
        "decision": record.decision,
        "decided_by_subject": record.decided_by_subject,
        "note": record.note,
        "comparison_snapshot": record.comparison_snapshot or {},
        "created_at": record.created_at,
    }


def _value_error_to_http(exc: ValueError) -> HTTPException:
    """Map service ValueError cases to safe HTTP responses.

    Only stable, internal strings are matched; no stack trace and no tenant
    existence signal is ever exposed. Foreign/missing objects collapse to a
    plain 404 "was not found"; contract violations (different target type,
    run not succeeded) map to 400; a blocked approval (Phase 1J.9B) maps to
    409 with the same stable detail — no metric values or customer data.
    """
    message = str(exc)
    if message == _APPROVAL_BLOCKED_ERROR:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        )
    if "was not found" in message:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=message,
    )


@router.get(
    "/runs",
    response_model=EvaluationRunListResponse,
)
async def list_evaluation_runs(
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationReadAuthz,
    limit: int = 100,
    offset: int = 0,
):
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    items = await AIEvaluationRepository.list_runs_for_tenant(
        db,
        organization_id=tenant.organization_id,
        limit=safe_limit,
        offset=safe_offset,
    )
    total = await AIEvaluationRepository.count_runs_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )

    return {
        "items": [_serialize_run(run) for run in items],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


@router.get(
    "/runs/{run_id}",
    response_model=EvaluationRunRead,
)
async def get_evaluation_run(
    run_id: str,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationReadAuthz,
):
    run = await AIEvaluationRepository.get_run_for_tenant(
        db,
        run_id=run_id,
        organization_id=tenant.organization_id,
    )

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation run {run_id} was not found.",
        )

    return _serialize_run(run)


@router.get(
    "/runs/{run_id}/cases",
    response_model=EvaluationCaseListResponse,
)
async def list_evaluation_run_cases(
    run_id: str,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationReadAuthz,
):
    run = await AIEvaluationRepository.get_run_for_tenant(
        db,
        run_id=run_id,
        organization_id=tenant.organization_id,
    )

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation run {run_id} was not found.",
        )

    cases = await AIEvaluationRepository.list_cases_for_run_for_tenant(
        db,
        run_id=run_id,
        organization_id=tenant.organization_id,
    )

    return {
        "items": [_serialize_case(case) for case in cases],
        "total": len(cases),
    }


@router.post(
    "/runs/rag",
    response_model=EvaluationRunQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_rag_evaluation(
    data: EvalRAGCaseInputs,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationManageAuthz,
):
    run = await AIEvaluationService.provision_rag_run(
        db,
        organization_id=tenant.organization_id,
        cases=[case.model_dump() for case in data.cases],
        trigger_source="manual",
        requested_by_subject=authz.subject,
    )

    queued = await IntegrationJobService.enqueue_ai_evaluation(
        db,
        evaluation_run_id=run.run_id,
        target_type="rag",
        organization_id=tenant.organization_id,
    )

    return {
        **_serialize_run(run),
        "job_id": queued["job_id"],
        "job_status": queued["status"],
    }


@router.post(
    "/runs/agent",
    response_model=EvaluationRunQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_agent_evaluation(
    data: EvalAgentCaseInputs,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationManageAuthz,
):
    run = await AIEvaluationService.provision_agent_run(
        db,
        organization_id=tenant.organization_id,
        cases=[case.model_dump() for case in data.cases],
        trigger_source="manual",
        requested_by_subject=authz.subject,
    )

    queued = await IntegrationJobService.enqueue_ai_evaluation(
        db,
        evaluation_run_id=run.run_id,
        target_type="agent",
        organization_id=tenant.organization_id,
    )

    return {
        **_serialize_run(run),
        "job_id": queued["job_id"],
        "job_status": queued["status"],
    }


@router.get(
    "/baselines",
    response_model=EvaluationBaselineListResponse,
)
async def list_evaluation_baselines(
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationReadAuthz,
    target_type: TargetType | None = None,
    limit: int = 100,
):
    safe_limit = max(1, min(limit, 200))

    items = await AIEvaluationRepository.list_baselines_for_tenant(
        db,
        organization_id=tenant.organization_id,
        target_type=target_type,
        limit=safe_limit,
    )

    return {
        "items": [_serialize_baseline(baseline) for baseline in items],
        "total": len(items),
    }


@router.post(
    "/runs/{run_id}/baseline",
    response_model=EvaluationBaselineRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_baseline_from_run(
    run_id: str,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationManageAuthz,
):
    try:
        baseline = await AIEvaluationService.create_baseline_from_run(
            db,
            organization_id=tenant.organization_id,
            run_id=run_id,
            created_by_subject=authz.subject,
        )
    except ValueError as exc:
        raise _value_error_to_http(exc) from None

    return _serialize_baseline(baseline)


@router.get(
    "/runs/{run_id}/compare/{baseline_id}",
    response_model=EvaluationRunComparison,
)
async def compare_run_to_baseline(
    run_id: str,
    baseline_id: int,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationReadAuthz,
):
    try:
        comparison = await AIEvaluationService.compare_run_to_baseline(
            db,
            organization_id=tenant.organization_id,
            run_id=run_id,
            baseline_id=baseline_id,
        )
    except ValueError as exc:
        raise _value_error_to_http(exc) from None

    return comparison


@router.post(
    "/runs/{run_id}/release-decision",
    response_model=EvaluationReleaseDecisionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_decision(
    run_id: str,
    data: EvaluationReleaseDecisionCreate,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationManageAuthz,
):
    try:
        record = await AIEvaluationService.record_release_decision(
            db,
            organization_id=tenant.organization_id,
            candidate_run_id=run_id,
            baseline_id=data.baseline_id,
            decision=data.decision,
            decided_by_subject=authz.subject,
            note=data.note,
        )
    except ValueError as exc:
        raise _value_error_to_http(exc) from None

    return _serialize_release_decision(record)


@router.get(
    "/release-decisions",
    response_model=EvaluationReleaseDecisionListResponse,
)
async def list_release_decisions(
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationReadAuthz,
    limit: int = 100,
    offset: int = 0,
):
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    items = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db,
        organization_id=tenant.organization_id,
        limit=safe_limit,
        offset=safe_offset,
    )
    total = await AIEvaluationRepository.count_release_decisions_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )

    return {
        "items": [_serialize_release_decision(record) for record in items],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


@router.get(
    "/release-decisions/{decision_id}",
    response_model=EvaluationReleaseDecisionRead,
)
async def get_release_decision(
    decision_id: int,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: EvaluationReadAuthz,
):
    record = await AIEvaluationRepository.get_release_decision_for_tenant(
        db,
        organization_id=tenant.organization_id,
        decision_id=decision_id,
    )

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Release decision was not found.",
        )

    return _serialize_release_decision(record)
