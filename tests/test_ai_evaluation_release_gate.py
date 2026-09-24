"""Phase 1J.9A release-gate check tests.

Service-level tests for ``AIEvaluationService.evaluate_release_gate``.
No real LLM: runs and baselines are persisted directly with the metric values
under test. Each scenario builds its own organization so tenants never mix.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.models.ai_evaluation_baseline import AIEvaluationBaseline
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.organization import Organization
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.services.ai_evaluation_service import AIEvaluationService

RAG_SAFE_METRICS = {"citation_validity": 0.8, "grounding_accuracy": 0.9}
AGENT_SAFE_METRICS = {"auto_execute_safety_accuracy": 0.9}

_created_org_ids: list[int] = []


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_evaluation_rows(db):
    yield
    if not _created_org_ids:
        return
    await db.execute(
        delete(AIEvaluationReleaseDecision).where(
            AIEvaluationReleaseDecision.organization_id.in_(_created_org_ids)
        )
    )
    await db.execute(
        delete(AIEvaluationRun).where(AIEvaluationRun.organization_id.in_(_created_org_ids))
    )
    await db.execute(
        delete(AIEvaluationBaseline).where(
            AIEvaluationBaseline.organization_id.in_(_created_org_ids)
        )
    )
    await db.commit()
    await db.execute(delete(Organization).where(Organization.id.in_(_created_org_ids)))
    await db.commit()
    _created_org_ids.clear()


async def _make_org(db, tag: str) -> Organization:
    org = Organization(name=f"gate-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    _created_org_ids.append(org.id)
    return org


async def _make_succeeded_run(
    db,
    *,
    organization_id: int,
    target_type: str,
    pass_rate: float,
    metrics: dict | None = None,
) -> AIEvaluationRun:
    run = await AIEvaluationRepository.create_run(
        db,
        run_id=uuid.uuid4().hex,
        organization_id=organization_id,
        target_type=target_type,
        model="test-model",
        status="running",
    )
    updated = await AIEvaluationRepository.update_run(
        db,
        run_id=run.run_id,
        organization_id=organization_id,
        values={
            "status": "succeeded",
            "pass_rate": pass_rate,
            "metrics": metrics or {},
            "completed_at": datetime.now(UTC),
        },
    )
    assert updated is not None
    return updated


async def _make_baseline(
    db,
    *,
    organization_id: int,
    run: AIEvaluationRun,
) -> AIEvaluationBaseline:
    return await AIEvaluationService.create_baseline_from_run(
        db, organization_id=organization_id, run_id=run.run_id
    )


async def _gate_setup(
    db,
    *,
    target_type: str = "rag",
    candidate_pass_rate: float = 0.9,
    baseline_pass_rate: float = 0.9,
    candidate_metrics: dict | None = None,
    baseline_metrics: dict | None = None,
):
    org = await _make_org(db, tag=target_type)
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        target_type=target_type,
        pass_rate=baseline_pass_rate,
        metrics=baseline_metrics or {},
    )
    baseline = await _make_baseline(db, organization_id=org.id, run=baseline_run)
    candidate = await _make_succeeded_run(
        db,
        organization_id=org.id,
        target_type=target_type,
        pass_rate=candidate_pass_rate,
        metrics=candidate_metrics or {},
    )
    return org, candidate, baseline


async def _evaluate(db, org, candidate, baseline):
    return await AIEvaluationService.evaluate_release_gate(
        db,
        organization_id=org.id,
        run_id=candidate.run_id,
        baseline_id=baseline.id,
    )


# --------------------------------------------------------------- RAG / critical


@pytest.mark.asyncio
async def test_rag_citation_validity_same_is_not_blocked(db):
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=dict(RAG_SAFE_METRICS),
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.target_type == "rag"
    assert result.critical_regressions == []
    assert result.warnings == []


@pytest.mark.asyncio
async def test_rag_citation_validity_improved_is_not_blocked(db):
    improved = dict(RAG_SAFE_METRICS)
    improved["citation_validity"] = 0.95
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=improved,
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.critical_regressions == []


@pytest.mark.asyncio
async def test_rag_citation_validity_regressed_is_blocked(db):
    regressed = dict(RAG_SAFE_METRICS)
    regressed["citation_validity"] = 0.6
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=regressed,
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is True
    assert len(result.critical_regressions) == 1
    issue = result.critical_regressions[0]
    assert issue.metric == "citation_validity"
    assert issue.kind == "critical"
    assert issue.baseline == 0.8
    assert issue.candidate == 0.6
    assert "Critical metric citation_validity regressed" in issue.message


@pytest.mark.asyncio
async def test_rag_grounding_accuracy_regressed_is_blocked(db):
    regressed = dict(RAG_SAFE_METRICS)
    regressed["grounding_accuracy"] = 0.5
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=regressed,
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is True
    assert len(result.critical_regressions) == 1
    issue = result.critical_regressions[0]
    assert issue.metric == "grounding_accuracy"
    assert issue.kind == "critical"


@pytest.mark.asyncio
async def test_rag_missing_citation_validity_is_blocked(db):
    missing = dict(RAG_SAFE_METRICS)
    del missing["citation_validity"]
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=missing,
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is True
    assert len(result.critical_regressions) == 1
    issue = result.critical_regressions[0]
    assert issue.metric == "citation_validity"
    assert issue.kind == "missing"
    assert issue.baseline is None
    assert issue.candidate is None
    assert issue.message == "Required critical metric citation_validity is missing."


@pytest.mark.asyncio
async def test_rag_missing_grounding_accuracy_is_blocked(db):
    missing = dict(RAG_SAFE_METRICS)
    del missing["grounding_accuracy"]
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=missing,
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is True
    assert result.critical_regressions[0].metric == "grounding_accuracy"
    assert result.critical_regressions[0].kind == "missing"


@pytest.mark.asyncio
async def test_rag_non_numeric_critical_is_conservatively_missing(db):
    corrupt = dict(RAG_SAFE_METRICS)
    corrupt["citation_validity"] = "not-a-number"
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=corrupt,
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is True
    assert result.critical_regressions[0].metric == "citation_validity"
    assert result.critical_regressions[0].kind == "missing"


# ------------------------------------------------------------- Agent / critical


@pytest.mark.asyncio
async def test_agent_auto_execute_safety_same_is_not_blocked(db):
    org, candidate, baseline = await _gate_setup(
        db,
        target_type="agent",
        candidate_metrics=dict(AGENT_SAFE_METRICS),
        baseline_metrics=dict(AGENT_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.target_type == "agent"
    assert result.critical_regressions == []
    assert result.warnings == []


@pytest.mark.asyncio
async def test_agent_auto_execute_safety_improved_is_not_blocked(db):
    improved = dict(AGENT_SAFE_METRICS)
    improved["auto_execute_safety_accuracy"] = 1.0
    org, candidate, baseline = await _gate_setup(
        db,
        target_type="agent",
        candidate_metrics=improved,
        baseline_metrics=dict(AGENT_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.critical_regressions == []


@pytest.mark.asyncio
async def test_agent_auto_execute_safety_regressed_is_blocked(db):
    regressed = dict(AGENT_SAFE_METRICS)
    regressed["auto_execute_safety_accuracy"] = 0.7
    org, candidate, baseline = await _gate_setup(
        db,
        target_type="agent",
        candidate_metrics=regressed,
        baseline_metrics=dict(AGENT_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is True
    assert len(result.critical_regressions) == 1
    issue = result.critical_regressions[0]
    assert issue.metric == "auto_execute_safety_accuracy"
    assert issue.kind == "critical"
    assert issue.message == "Critical metric auto_execute_safety_accuracy regressed."


@pytest.mark.asyncio
async def test_agent_missing_auto_execute_safety_is_blocked(db):
    missing = {}
    org, candidate, baseline = await _gate_setup(
        db,
        target_type="agent",
        candidate_metrics=missing,
        baseline_metrics=dict(AGENT_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is True
    assert len(result.critical_regressions) == 1
    issue = result.critical_regressions[0]
    assert issue.metric == "auto_execute_safety_accuracy"
    assert issue.kind == "missing"
    assert issue.message == "Required critical metric auto_execute_safety_accuracy is missing."


# -------------------------------------------------------------- non-critical


@pytest.mark.asyncio
async def test_pass_rate_regression_is_warning_only(db):
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_pass_rate=0.5,
        baseline_pass_rate=0.9,
        candidate_metrics=dict(RAG_SAFE_METRICS),
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.critical_regressions == []
    assert [w.metric for w in result.warnings] == ["pass_rate"]
    warning = result.warnings[0]
    assert warning.kind == "warning"
    assert warning.baseline == 0.9
    assert warning.candidate == 0.5
    assert warning.message == "Metric pass_rate regressed."


@pytest.mark.asyncio
async def test_retrieval_accuracy_regression_is_warning_only(db):
    baseline_metrics = {"retrieval_accuracy": 0.9, **RAG_SAFE_METRICS}
    candidate_metrics = {"retrieval_accuracy": 0.5, **RAG_SAFE_METRICS}
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.critical_regressions == []
    assert [w.metric for w in result.warnings] == ["retrieval_accuracy"]
    assert result.warnings[0].kind == "warning"


@pytest.mark.asyncio
async def test_agent_non_critical_regression_is_warning_only(db):
    baseline_metrics = {
        "auto_execute_safety_accuracy": 0.9,
        "tool_accuracy": 0.9,
        "action_accuracy": 0.9,
    }
    candidate_metrics = {
        "auto_execute_safety_accuracy": 0.9,
        "tool_accuracy": 0.9,
        "action_accuracy": 0.6,
    }
    org, candidate, baseline = await _gate_setup(
        db,
        target_type="agent",
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.critical_regressions == []
    assert [w.metric for w in result.warnings] == ["action_accuracy"]


# ------------------------------------------------------------------ general


@pytest.mark.asyncio
async def test_no_regressions_is_not_blocked_with_empty_warnings(db):
    metrics = dict(RAG_SAFE_METRICS)
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=metrics,
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False
    assert result.critical_regressions == []
    assert result.warnings == []


@pytest.mark.asyncio
async def test_foreign_run_uses_existing_safe_error(db):
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=dict(RAG_SAFE_METRICS),
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    other_org = await _make_org(db, tag="other")
    foreign_run = await _make_succeeded_run(
        db,
        organization_id=other_org.id,
        target_type="rag",
        pass_rate=0.9,
        metrics=dict(RAG_SAFE_METRICS),
    )
    with pytest.raises(ValueError, match="was not found for this organization"):
        await AIEvaluationService.evaluate_release_gate(
            db,
            organization_id=org.id,
            run_id=foreign_run.run_id,
            baseline_id=baseline.id,
        )
    with pytest.raises(ValueError, match="was not found for this organization"):
        await AIEvaluationService.evaluate_release_gate(
            db,
            organization_id=other_org.id,
            run_id=candidate.run_id,
            baseline_id=baseline.id,
        )


@pytest.mark.asyncio
async def test_foreign_baseline_uses_existing_safe_error(db):
    org, candidate, _baseline = await _gate_setup(
        db,
        candidate_metrics=dict(RAG_SAFE_METRICS),
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    other_org = await _make_org(db, tag="other")
    foreign_baseline = await _make_baseline(
        db,
        organization_id=other_org.id,
        run=await _make_succeeded_run(
            db,
            organization_id=other_org.id,
            target_type="rag",
            pass_rate=0.9,
            metrics=dict(RAG_SAFE_METRICS),
        ),
    )
    with pytest.raises(ValueError, match="was not found for this organization"):
        await AIEvaluationService.evaluate_release_gate(
            db,
            organization_id=org.id,
            run_id=candidate.run_id,
            baseline_id=foreign_baseline.id,
        )


@pytest.mark.asyncio
async def test_different_target_type_uses_existing_safe_error(db):
    org, _candidate, baseline = await _gate_setup(
        db,
        target_type="rag",
        candidate_metrics=dict(RAG_SAFE_METRICS),
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    agent_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        target_type="agent",
        pass_rate=0.9,
        metrics=dict(AGENT_SAFE_METRICS),
    )
    with pytest.raises(ValueError, match="different target type"):
        await AIEvaluationService.evaluate_release_gate(
            db,
            organization_id=org.id,
            run_id=agent_run.run_id,
            baseline_id=baseline.id,
        )


@pytest.mark.asyncio
async def test_gate_is_pure_read_only(db):
    org, candidate, baseline = await _gate_setup(
        db,
        candidate_metrics=dict(RAG_SAFE_METRICS),
        baseline_metrics=dict(RAG_SAFE_METRICS),
    )
    runs_before = len(await AIEvaluationRepository.list_runs_for_tenant(db, organization_id=org.id))
    baselines_before = len(
        await AIEvaluationRepository.list_baselines_for_tenant(db, organization_id=org.id)
    )
    decisions_before = len(
        await AIEvaluationRepository.list_release_decisions_for_tenant(db, organization_id=org.id)
    )

    result = await _evaluate(db, org, candidate, baseline)
    assert result.blocked is False

    runs_after = len(await AIEvaluationRepository.list_runs_for_tenant(db, organization_id=org.id))
    baselines_after = len(
        await AIEvaluationRepository.list_baselines_for_tenant(db, organization_id=org.id)
    )
    decisions_after = len(
        await AIEvaluationRepository.list_release_decisions_for_tenant(db, organization_id=org.id)
    )
    assert runs_after == runs_before
    assert baselines_after == baselines_before
    assert decisions_after == decisions_before == 0
