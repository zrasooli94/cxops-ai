"""Phase 1J.7A — baseline + simple comparison service tests.

Covers the required scenarios:

1.  succeeded run → baseline (promoted, copies run fields)
2.  queued run → cannot promote (safe error)
3.  failed run → cannot promote (safe error)
4.  second baseline → version 2, first baseline kept
5.  history is never deleted (older baselines remain)
6.  pass_rate delta is correct
7.  candidate pass_rate above baseline → improved
8.  candidate pass_rate below baseline → regressed
9.  equal pass rates → same
10. metric present on only one side is ignored
11. RAG run vs Agent baseline (or vice versa) → safe error
12. tenant A cannot use tenant B baseline
13. tenant A cannot compare tenant B run
14. cases_count comes from persisted case rows

No real LLM is ever called.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.services.ai_evaluation_service import AIEvaluationService


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _make_org(db, tag: str) -> Organization:
    org = Organization(name=f"eval-baseline-{tag}-{uuid.uuid4().hex[:8]}")
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
):
    run = await _make_run(
        db,
        organization_id=organization_id,
        target_type=target_type,
        status="running",
    )
    await _add_cases(db, run_id=run.run_id, organization_id=organization_id, count=case_count)
    await AIEvaluationRepository.update_run(
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
    return await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run.run_id, organization_id=organization_id
    )


# ---------------------------------------------------------------- baseline


@pytest.mark.asyncio
async def test_succeeded_run_becomes_promoted_baseline(db):
    org = await _make_org(db, "ok")
    run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.92,
        metrics={"retrieval_accuracy": 1.0},
        case_count=3,
    )

    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=run.run_id
    )

    assert baseline is not None
    assert baseline.organization_id == org.id
    assert baseline.target_type == "rag"
    assert baseline.version == "1"
    assert baseline.model == run.model
    assert baseline.embedding_model == run.embedding_model
    assert baseline.agent_decision_version == run.agent_decision_version
    assert baseline.tool_policy_version == run.tool_policy_version
    assert baseline.corpus_revision == run.corpus_revision
    assert baseline.pass_rate == 0.92
    assert baseline.metrics == {"retrieval_accuracy": 1.0}
    assert baseline.cases_count == 3
    assert baseline.promoted is True


@pytest.mark.asyncio
async def test_queued_run_cannot_become_baseline(db):
    org = await _make_org(db, "queued")
    run = await _make_run(db, organization_id=org.id, status="queued")

    with pytest.raises(ValueError, match="only succeeded"):
        await AIEvaluationService.create_baseline_from_run(
            db, organization_id=org.id, run_id=run.run_id
        )


@pytest.mark.asyncio
async def test_failed_run_cannot_become_baseline(db):
    org = await _make_org(db, "failed")
    run = await _make_run(db, organization_id=org.id, status="failed")

    with pytest.raises(ValueError, match="only succeeded"):
        await AIEvaluationService.create_baseline_from_run(
            db, organization_id=org.id, run_id=run.run_id
        )


@pytest.mark.asyncio
async def test_second_baseline_gets_version_two_and_history_kept(db):
    org = await _make_org(db, "v2")
    run_one = await _make_succeeded_run(db, organization_id=org.id, pass_rate=0.8)
    run_two = await _make_succeeded_run(db, organization_id=org.id, pass_rate=0.9)

    baseline_one = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=run_one.run_id
    )
    baseline_two = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=run_two.run_id
    )

    assert baseline_one.version == "1"
    assert baseline_two.version == "2"
    assert baseline_one.id != baseline_two.id


@pytest.mark.asyncio
async def test_older_baselines_are_never_deleted(db):
    org = await _make_org(db, "history")
    runs = [
        await _make_succeeded_run(db, organization_id=org.id, pass_rate=0.7 + index * 0.1)
        for index in range(3)
    ]
    for run in runs:
        await AIEvaluationService.create_baseline_from_run(
            db, organization_id=org.id, run_id=run.run_id
        )

    history = await AIEvaluationRepository.list_baselines_for_tenant(
        db, organization_id=org.id, target_type="rag"
    )
    assert sorted(b.version for b in history) == ["1", "2", "3"]
    assert all(b.promoted for b in history)


# -------------------------------------------------------------- comparison


@pytest.mark.asyncio
async def test_pass_rate_delta_is_correct(db):
    org = await _make_org(db, "delta")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.80,
        metrics={"retrieval_accuracy": 0.95},
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=baseline_run.run_id
    )
    candidate_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.90,
        metrics={"retrieval_accuracy": 1.0},
    )

    comparison = await AIEvaluationService.compare_run_to_baseline(
        db, organization_id=org.id, run_id=candidate_run.run_id, baseline_id=baseline.id
    )

    assert comparison.target_type == "rag"
    assert comparison.baseline_pass_rate == 0.80
    assert comparison.candidate_pass_rate == 0.90
    assert comparison.pass_rate_delta == 0.10


@pytest.mark.asyncio
async def test_higher_candidate_pass_rate_is_improved(db):
    org = await _make_org(db, "improved")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.80,
        metrics={"retrieval_accuracy": 0.9, "answer_correctness": 0.9},
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=baseline_run.run_id
    )
    candidate_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.90,
        metrics={"retrieval_accuracy": 1.0, "answer_correctness": 0.9},
    )

    comparison = await AIEvaluationService.compare_run_to_baseline(
        db, organization_id=org.id, run_id=candidate_run.run_id, baseline_id=baseline.id
    )

    by_metric = {m.metric: m for m in comparison.metrics}
    assert by_metric["retrieval_accuracy"].direction == "improved"
    assert by_metric["retrieval_accuracy"].delta == 0.10
    assert by_metric["answer_correctness"].direction == "same"
    assert by_metric["answer_correctness"].delta == 0.0
    assert len(comparison.metrics) == 2


@pytest.mark.asyncio
async def test_lower_candidate_pass_rate_is_regressed(db):
    org = await _make_org(db, "regressed")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.90,
        metrics={"retrieval_accuracy": 1.0},
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=baseline_run.run_id
    )
    candidate_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.80,
        metrics={"retrieval_accuracy": 0.8},
    )

    comparison = await AIEvaluationService.compare_run_to_baseline(
        db, organization_id=org.id, run_id=candidate_run.run_id, baseline_id=baseline.id
    )

    assert comparison.pass_rate_delta < 0
    assert comparison.metrics[0].metric == "retrieval_accuracy"
    assert comparison.metrics[0].direction == "regressed"


@pytest.mark.asyncio
async def test_equal_pass_rates_are_same(db):
    org = await _make_org(db, "same")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.85,
        metrics={"retrieval_accuracy": 1.0},
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=baseline_run.run_id
    )
    candidate_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.85,
        metrics={"retrieval_accuracy": 1.0},
    )

    comparison = await AIEvaluationService.compare_run_to_baseline(
        db, organization_id=org.id, run_id=candidate_run.run_id, baseline_id=baseline.id
    )

    assert comparison.pass_rate_delta == 0.0
    assert comparison.metrics[0].direction == "same"


@pytest.mark.asyncio
async def test_metric_present_on_one_side_is_ignored(db):
    org = await _make_org(db, "partial")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.5,
        metrics={"retrieval_accuracy": 1.0},
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=baseline_run.run_id
    )
    candidate_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.5,
        metrics={"answer_correctness": 1.0},
    )

    comparison = await AIEvaluationService.compare_run_to_baseline(
        db, organization_id=org.id, run_id=candidate_run.run_id, baseline_id=baseline.id
    )

    assert comparison.metrics == []


@pytest.mark.asyncio
async def test_matching_metrics_only_compared(db):
    org = await _make_org(db, "intersect")
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.5,
        metrics={"retrieval_accuracy": 0.9, "answer_correctness": 0.9},
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=baseline_run.run_id
    )
    candidate_run = await _make_succeeded_run(
        db,
        organization_id=org.id,
        pass_rate=0.5,
        metrics={"retrieval_accuracy": 1.0, "grounding_accuracy": 1.0},
    )

    comparison = await AIEvaluationService.compare_run_to_baseline(
        db, organization_id=org.id, run_id=candidate_run.run_id, baseline_id=baseline.id
    )

    assert [m.metric for m in comparison.metrics] == ["retrieval_accuracy"]


@pytest.mark.asyncio
async def test_run_of_different_target_type_is_rejected(db):
    org = await _make_org(db, "mismatch")
    baseline_run = await _make_succeeded_run(
        db, organization_id=org.id, target_type="rag", pass_rate=0.5
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org.id, run_id=baseline_run.run_id
    )
    agent_run = await _make_succeeded_run(
        db, organization_id=org.id, target_type="agent", pass_rate=0.5
    )

    with pytest.raises(ValueError, match="different target type"):
        await AIEvaluationService.compare_run_to_baseline(
            db, organization_id=org.id, run_id=agent_run.run_id, baseline_id=baseline.id
        )


# ---------------------------------------------------------------- tenancy


@pytest.mark.asyncio
async def test_tenant_a_cannot_use_tenant_b_baseline(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline_run_b = await _make_succeeded_run(db, organization_id=org_b.id)
    baseline_b = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_b.id, run_id=baseline_run_b.run_id
    )

    with pytest.raises(ValueError, match="not found"):
        await AIEvaluationService.compare_run_to_baseline(
            db, organization_id=org_a.id, run_id=run_a.run_id, baseline_id=baseline_b.id
        )


@pytest.mark.asyncio
async def test_tenant_a_cannot_compare_tenant_b_run(db):
    org_a = await _make_org(db, "c")
    org_b = await _make_org(db, "d")
    baseline_run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline_a = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=baseline_run_a.run_id
    )
    run_b = await _make_succeeded_run(db, organization_id=org_b.id)

    with pytest.raises(ValueError, match="not found"):
        await AIEvaluationService.compare_run_to_baseline(
            db, organization_id=org_a.id, run_id=run_b.run_id, baseline_id=baseline_a.id
        )


@pytest.mark.asyncio
async def test_missing_run_errors_for_baseline_creation(db):
    org = await _make_org(db, "missing")

    with pytest.raises(ValueError, match="not found"):
        await AIEvaluationService.create_baseline_from_run(
            db, organization_id=org.id, run_id="no-such-run"
        )