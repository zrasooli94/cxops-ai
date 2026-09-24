"""Phase 1J.8A — release decision backend foundation tests.

Covers the required scenarios:

1.  approved decision is saved
2.  rejected decision is saved
3.  decided_by_subject is saved
4.  comparison snapshot is saved
5.  old decision remains when a second one is added
6.  queued candidate cannot be decided (safe error)
7.  failed candidate cannot be decided (safe error)
8.  RAG candidate vs Agent baseline (or vice versa) → safe error
9.  tenant A cannot decide tenant B run
10. tenant A cannot use tenant B baseline
11. cross-tenant relationship is rejected at the database level
12. invalid decision value is rejected
13. note length is bounded
14. sensitive run.input is never copied into the snapshot

No real LLM is ever called.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.organization import Organization
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.services.ai_evaluation_service import AIEvaluationService

_RAG_SAFE_METRICS = {"citation_validity": 0.85, "grounding_accuracy": 0.85}

_AGENT_SAFE_METRICS = {"auto_execute_safety_accuracy": 0.85}


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _make_org(db, tag: str) -> Organization:
    org = Organization(name=f"eval-decision-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    return org


def _run_id(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex}"


async def _make_run(
    db,
    *,
    organization_id: int,
    target_type: str = "rag",
    status: str = "running",
    model: str = "gpt-test",
    embedding_model: str = "text-embed-3",
    agent_decision_version: str | None = "2",
    tool_policy_version: int | None = 1,
    corpus_revision: dict | None = None,
    input_data: dict | None = None,
):
    return await AIEvaluationRepository.create_run(
        db,
        run_id=_run_id("run"),
        organization_id=organization_id,
        target_type=target_type,
        model=model,
        embedding_model=embedding_model,
        agent_decision_version=agent_decision_version,
        tool_policy_version=tool_policy_version,
        corpus_revision=corpus_revision or {"documents": 7},
        status=status,
        input_data=input_data,
    )


async def _add_cases(db, *, run_id: str, organization_id: int, count: int):
    for index in range(count):
        await AIEvaluationRepository.add_case(
            db,
            run_id=run_id,
            organization_id=organization_id,
            case_id=f"case-{index}",
            case_type="rag",
        )


async def _make_succeeded_run(
    db,
    *,
    organization_id: int,
    target_type: str = "rag",
    pass_rate: float = 0.9,
    metrics: dict | None = None,
    case_count: int = 2,
    input_data: dict | None = None,
):
    run = await _make_run(
        db,
        organization_id=organization_id,
        target_type=target_type,
        status="running",
        input_data=input_data,
    )
    await _add_cases(db, run_id=run.run_id, organization_id=organization_id, count=case_count)
    await AIEvaluationRepository.update_run(
        db,
        run_id=run.run_id,
        organization_id=organization_id,
        values={
            "status": "succeeded",
            "pass_rate": pass_rate,
            "metrics": metrics if metrics is not None else _RAG_SAFE_METRICS,
            "completed_at": datetime.now(UTC),
        },
    )
    return await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run.run_id, organization_id=organization_id
    )


async def _make_baseline(db, *, organization_id: int, run_id: str):
    return await AIEvaluationService.create_baseline_from_run(
        db, organization_id=organization_id, run_id=run_id
    )


# ---------------------------------------------------------------- decisions


@pytest.mark.asyncio
async def test_approved_decision_is_saved(db):
    org = await _make_org(db, "approved")
    run = await _make_succeeded_run(db, organization_id=org.id)
    baseline = await _make_baseline(db, organization_id=org.id, run_id=run.run_id)

    record = await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=run.run_id,
        baseline_id=baseline.id,
        decision="approved",
        decided_by_subject="subject-1",
    )

    assert record is not None
    assert record.decision == "approved"
    assert record.organization_id == org.id
    assert record.candidate_run_id == run.id
    assert record.baseline_id == baseline.id


@pytest.mark.asyncio
async def test_rejected_decision_is_saved(db):
    org = await _make_org(db, "rejected")
    run = await _make_succeeded_run(db, organization_id=org.id)
    baseline = await _make_baseline(db, organization_id=org.id, run_id=run.run_id)

    record = await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=run.run_id,
        baseline_id=baseline.id,
        decision="rejected",
        decided_by_subject="subject-1",
    )

    assert record.decision == "rejected"


@pytest.mark.asyncio
async def test_decided_by_subject_is_saved(db):
    org = await _make_org(db, "actor")
    run = await _make_succeeded_run(db, organization_id=org.id)
    baseline = await _make_baseline(db, organization_id=org.id, run_id=run.run_id)

    record = await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=run.run_id,
        baseline_id=baseline.id,
        decision="approved",
        decided_by_subject="zs@example.com (Zaker)",
    )

    assert record.decided_by_subject == "zs@example.com (Zaker)"


@pytest.mark.asyncio
async def test_comparison_snapshot_is_saved(db):
    org = await _make_org(db, "snapshot")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.80,
        metrics={"retrieval_accuracy": 0.95, **_RAG_SAFE_METRICS},
    )
    baseline = await _make_baseline(db, organization_id=org.id, run_id=baseline_run.run_id)
    candidate_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.90,
        metrics={"retrieval_accuracy": 1.0, **_RAG_SAFE_METRICS},
    )

    record = await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=candidate_run.run_id,
        baseline_id=baseline.id,
        decision="approved",
        decided_by_subject="subject-1",
    )

    snapshot = record.comparison_snapshot
    assert snapshot["target_type"] == "rag"
    assert snapshot["candidate_pass_rate"] == 0.90
    assert snapshot["baseline_pass_rate"] == 0.80
    assert snapshot["pass_rate_delta"] == 0.10
    assert {m["metric"] for m in snapshot["metrics"]} == {
        "retrieval_accuracy",
        "citation_validity",
        "grounding_accuracy",
    }
    assert snapshot["candidate"]["model"] == candidate_run.model
    assert snapshot["baseline"]["model"] == baseline_run.model


@pytest.mark.asyncio
async def test_old_decision_remains_when_second_added(db):
    org = await _make_org(db, "history")
    run = await _make_succeeded_run(db, organization_id=org.id)
    baseline = await _make_baseline(db, organization_id=org.id, run_id=run.run_id)

    first = await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=run.run_id,
        baseline_id=baseline.id,
        decision="approved",
        decided_by_subject="subject-1",
    )
    second = await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=run.run_id,
        baseline_id=baseline.id,
        decision="rejected",
        decided_by_subject="subject-2",
    )

    assert first.id != second.id
    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert {record.id for record in history} == {first.id, second.id}
    assert {record.decision for record in history} == {"approved", "rejected"}


@pytest.mark.asyncio
async def test_queued_candidate_cannot_be_decided(db):
    org = await _make_org(db, "queued")
    run = await _make_run(db, organization_id=org.id, status="queued")
    baseline = await _make_baseline(
        db,
        organization_id=org.id,
        run_id=(await _make_succeeded_run(db, organization_id=org.id, pass_rate=0.5)).run_id,
    )

    with pytest.raises(ValueError, match="only succeeded"):
        await AIEvaluationService.record_release_decision(
            db,
            organization_id=org.id,
            candidate_run_id=run.run_id,
            baseline_id=baseline.id,
            decision="approved",
            decided_by_subject="subject-1",
        )


@pytest.mark.asyncio
async def test_failed_candidate_cannot_be_decided(db):
    org = await _make_org(db, "failed")
    run = await _make_run(db, organization_id=org.id, status="failed")
    baseline = await _make_baseline(
        db,
        organization_id=org.id,
        run_id=(await _make_succeeded_run(db, organization_id=org.id, pass_rate=0.5)).run_id,
    )

    with pytest.raises(ValueError, match="only succeeded"):
        await AIEvaluationService.record_release_decision(
            db,
            organization_id=org.id,
            candidate_run_id=run.run_id,
            baseline_id=baseline.id,
            decision="approved",
            decided_by_subject="subject-1",
        )


@pytest.mark.asyncio
async def test_candidate_and_baseline_of_different_target_types_rejected(db):
    org = await _make_org(db, "mismatch")
    rag_run = await _make_succeeded_run(
        db, organization_id=org.id, target_type="rag", pass_rate=0.5
    )
    baseline = await _make_baseline(db, organization_id=org.id, run_id=rag_run.run_id)
    agent_run = await _make_succeeded_run(
        db, organization_id=org.id, target_type="agent", pass_rate=0.5
    )

    with pytest.raises(ValueError, match="different target types"):
        await AIEvaluationService.record_release_decision(
            db,
            organization_id=org.id,
            candidate_run_id=agent_run.run_id,
            baseline_id=baseline.id,
            decision="approved",
            decided_by_subject="subject-1",
        )


@pytest.mark.asyncio
async def test_foreign_candidate_is_rejected(db):
    org_a = await _make_org(db, "runa")
    org_b = await _make_org(db, "runb")
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    run_b = await _make_succeeded_run(db, organization_id=org_b.id)
    baseline = await _make_baseline(db, organization_id=org_a.id, run_id=run_a.run_id)

    with pytest.raises(ValueError, match="not found"):
        await AIEvaluationService.record_release_decision(
            db,
            organization_id=org_a.id,
            candidate_run_id=run_b.run_id,
            baseline_id=baseline.id,
            decision="approved",
            decided_by_subject="subject-1",
        )


@pytest.mark.asyncio
async def test_foreign_baseline_is_rejected(db):
    org_a = await _make_org(db, "basea")
    org_b = await _make_org(db, "baseb")
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    run_b = await _make_succeeded_run(db, organization_id=org_b.id)
    baseline_b = await _make_baseline(db, organization_id=org_b.id, run_id=run_b.run_id)

    with pytest.raises(ValueError, match="not found"):
        await AIEvaluationService.record_release_decision(
            db,
            organization_id=org_a.id,
            candidate_run_id=run_a.run_id,
            baseline_id=baseline_b.id,
            decision="approved",
            decided_by_subject="subject-1",
        )


@pytest.mark.asyncio
async def test_cross_tenant_relationship_rejected_at_database(db):
    org_a = await _make_org(db, "db-a")
    org_b = await _make_org(db, "db-b")
    run_b = await _make_succeeded_run(db, organization_id=org_b.id)
    baseline_b = await _make_baseline(db, organization_id=org_b.id, run_id=run_b.run_id)

    record = AIEvaluationReleaseDecision(
        organization_id=org_a.id,
        candidate_run_id=run_b.id,
        baseline_id=baseline_b.id,
        decision="approved",
        decided_by_subject="subject-1",
    )
    db.add(record)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_invalid_decision_is_rejected(db):
    org = await _make_org(db, "invalid")
    run = await _make_succeeded_run(db, organization_id=org.id)
    baseline = await _make_baseline(db, organization_id=org.id, run_id=run.run_id)

    with pytest.raises(ValueError, match="approved.*rejected"):
        await AIEvaluationService.record_release_decision(
            db,
            organization_id=org.id,
            candidate_run_id=run.run_id,
            baseline_id=baseline.id,
            decision="maybe",
            decided_by_subject="subject-1",
        )


@pytest.mark.asyncio
async def test_note_length_is_bounded(db):
    org = await _make_org(db, "note")
    run = await _make_succeeded_run(db, organization_id=org.id)
    baseline = await _make_baseline(db, organization_id=org.id, run_id=run.run_id)

    with pytest.raises(ValueError, match="at most 1000"):
        await AIEvaluationService.record_release_decision(
            db,
            organization_id=org.id,
            candidate_run_id=run.run_id,
            baseline_id=baseline.id,
            decision="approved",
            decided_by_subject="subject-1",
            note="x" * 1001,
        )


@pytest.mark.asyncio
async def test_sensitive_run_input_not_copied_into_snapshot(db):
    org = await _make_org(db, "piisafe")
    run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.90,
        metrics={"retrieval_accuracy": 1.0, **_RAG_SAFE_METRICS},
        input_data={
            "cases": [
                {
                    "question": "Refund for account SECRET-ACCT-912",
                    "expected_sources": ["https://secret.example.com/ticket"],
                    "expected_terms": ["refund"],
                }
            ]
        },
    )
    baseline = await _make_baseline(db, organization_id=org.id, run_id=run.run_id)

    record = await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=run.run_id,
        baseline_id=baseline.id,
        decision="approved",
        decided_by_subject="subject-1",
    )

    dumped = json.dumps(record.comparison_snapshot)
    assert "SECRET-ACCT-912" not in dumped
    assert "secret.example.com" not in dumped
    assert "cases" not in record.comparison_snapshot
    assert set(record.comparison_snapshot) == {
        "target_type",
        "candidate_pass_rate",
        "baseline_pass_rate",
        "pass_rate_delta",
        "metrics",
        "baseline",
        "candidate",
    }


# ------------------------------------------------- gate enforcement on approve (1J.9B)


async def _blocked_rag_comparison(db, org, *, regress: str | None = None):
    """A baseline + candidate whose critical metrics are safe on the baseline.

    Returns ``(baseline, candidate)``. The candidate regresses ``regress``
    (a critical metric key) below the baseline when given.
    """
    baseline_run = await _make_succeeded_run(
        db, organization_id=org.id, metrics=dict(_RAG_SAFE_METRICS)
    )
    baseline = await _make_baseline(db, organization_id=org.id, run_id=baseline_run.run_id)
    if regress is None:
        candidate_metrics = dict(_RAG_SAFE_METRICS)
    else:
        candidate_metrics = dict(_RAG_SAFE_METRICS)
        candidate_metrics[regress] = 0.5
    candidate = await _make_succeeded_run(db, organization_id=org.id, metrics=candidate_metrics)
    return baseline, candidate


async def _decide(db, org, *, candidate, baseline, decision="approved"):
    return await AIEvaluationService.record_release_decision(
        db,
        organization_id=org.id,
        candidate_run_id=candidate.run_id,
        baseline_id=baseline.id,
        decision=decision,
        decided_by_subject="subject-1",
    )


@pytest.mark.asyncio
async def test_approve_with_safe_critical_metrics_is_saved(db):
    org = await _make_org(db, "gw-ok")
    baseline, candidate = await _blocked_rag_comparison(db, org)
    record = await _decide(db, org, candidate=candidate, baseline=baseline)
    assert record.decision == "approved"
    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert len(history) == 1


@pytest.mark.asyncio
async def test_approve_with_critical_regression_is_blocked_and_writes_nothing(db):
    org = await _make_org(db, "gw-bad")
    baseline, candidate = await _blocked_rag_comparison(db, org, regress="citation_validity")

    with pytest.raises(ValueError, match=r"Approval blocked by critical evaluation regression\."):
        await _decide(db, org, candidate=candidate, baseline=baseline)

    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert history == []


@pytest.mark.asyncio
async def test_approve_with_missing_critical_metric_is_blocked_and_writes_nothing(db):
    org = await _make_org(db, "gw-miss")
    baseline, _ = await _blocked_rag_comparison(db, org)
    missing = dict(_RAG_SAFE_METRICS)
    del missing["grounding_accuracy"]
    candidate = await _make_succeeded_run(db, organization_id=org.id, metrics=missing)

    with pytest.raises(ValueError, match=r"Approval blocked by critical evaluation regression\."):
        await _decide(db, org, candidate=candidate, baseline=baseline)

    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert history == []


@pytest.mark.asyncio
async def test_reject_with_critical_regression_is_saved(db):
    org = await _make_org(db, "gw-rej")
    baseline, candidate = await _blocked_rag_comparison(db, org, regress="grounding_accuracy")

    record = await _decide(db, org, candidate=candidate, baseline=baseline, decision="rejected")

    assert record.decision == "rejected"
    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert len(history) == 1


@pytest.mark.asyncio
async def test_reject_with_missing_critical_metric_is_saved(db):
    org = await _make_org(db, "gw-rejm")
    baseline, _ = await _blocked_rag_comparison(db, org)
    missing = dict(_RAG_SAFE_METRICS)
    del missing["citation_validity"]
    candidate = await _make_succeeded_run(db, organization_id=org.id, metrics=missing)

    record = await _decide(db, org, candidate=candidate, baseline=baseline, decision="rejected")

    assert record.decision == "rejected"
    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert len(history) == 1


@pytest.mark.asyncio
async def test_warning_only_regression_still_allows_approval(db):
    org = await _make_org(db, "gw-warn")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.9,
        metrics={"retrieval_accuracy": 0.9, **_RAG_SAFE_METRICS},
    )
    baseline = await _make_baseline(db, organization_id=org.id, run_id=baseline_run.run_id)
    candidate = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.5,
        metrics={"retrieval_accuracy": 0.5, **_RAG_SAFE_METRICS},
    )

    record = await _decide(db, org, candidate=candidate, baseline=baseline)

    assert record.decision == "approved"
    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert len(history) == 1


@pytest.mark.asyncio
async def test_old_decisions_remain_when_later_approval_is_blocked(db):
    org = await _make_org(db, "gw-hist")
    baseline, safe_candidate = await _blocked_rag_comparison(db, org)
    first = await _decide(db, org, candidate=safe_candidate, baseline=baseline)
    assert first.decision == "approved"

    _, blocked_candidate = await _blocked_rag_comparison(db, org, regress="citation_validity")
    with pytest.raises(ValueError, match=r"Approval blocked by critical evaluation regression\."):
        await _decide(db, org, candidate=blocked_candidate, baseline=baseline)

    history = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org.id
    )
    assert [record.id for record in history] == [first.id]
    assert {record.decision for record in history} == {"approved"}
