"""Phase 1J.1A — AI evaluation database foundation: model-level tests.

Small, focused model tests for the three evaluation tables:

- models persist with the repository's tenant-owned defaults
- run → case relationship resolves through the composite FK
- duplicate run_id is rejected
- duplicate case_id within the same run is rejected
- different tenants remain separate
- a case can never attach to a run owned by a different tenant
- baseline history is retained (no unique single-baseline replacement)
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.ai_evaluation_baseline import AIEvaluationBaseline
from app.models.ai_evaluation_case import AIEvaluationCase
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.organization import Organization


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _make_org(db, tag: str) -> Organization:
    org = Organization(name=f"ai-eval-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _make_run(
    db,
    *,
    organization_id: int,
    run_id: str,
    target_type: str = "agent",
    model: str = "gpt-test",
) -> AIEvaluationRun:
    run = AIEvaluationRun(
        run_id=run_id,
        organization_id=organization_id,
        target_type=target_type,
        model=model,
        embedding_model="test-embed",
        agent_decision_version="2",
        tool_policy_version=1,
    )
    db.add(run)
    await db.flush()
    return run


async def _make_case(db, *, run, organization_id: int, case_id: str) -> AIEvaluationCase:
    case = AIEvaluationCase(
        run_id=run.id,
        organization_id=organization_id,
        case_id=case_id,
        case_type=run.target_type,
        expected={"expected_action": "no_action"},
        actual={"actual_action": "no_action"},
        dimensions={"action_pass": True},
    )
    db.add(case)
    await db.flush()
    return case


@pytest.mark.asyncio
async def test_evaluation_models_persist_with_defaults(db):
    org = await _make_org(db, "persist")
    run = await _make_run(db, organization_id=org.id, run_id=f"run-{uuid.uuid4().hex}")
    await _make_case(db, run=run, organization_id=org.id, case_id="case-1")
    baseline = AIEvaluationBaseline(
        organization_id=org.id,
        target_type="agent",
        version="1.1.0",
        model="gpt-test",
        embedding_model="test-embed",
        agent_decision_version="2",
        tool_policy_version=1,
        pass_rate=0.9,
        metrics={"action_accuracy": 0.9},
        cases_count=1,
    )
    db.add(baseline)
    await db.commit()

    assert run.id is not None
    assert run.status == "queued"
    assert run.trigger_source == "manual"
    assert run.metrics == {}
    assert run.pass_rate is None
    assert run.created_at is not None
    assert run.started_at is None
    assert run.completed_at is None

    case_rows = (
        (await db.execute(select(AIEvaluationCase).where(AIEvaluationCase.run_id == run.id)))
        .scalars()
        .all()
    )
    assert len(case_rows) == 1
    case = case_rows[0]
    assert case.case_id == "case-1"
    assert case.organization_id == org.id
    assert case.case_type == "agent"
    assert case.created_at is not None

    assert baseline.organization_id == org.id
    assert baseline.cases_count == 1
    assert baseline.promoted is False
    assert baseline.created_at is not None


@pytest.mark.asyncio
async def test_run_case_relationship_resolves(db):
    org = await _make_org(db, "rel")
    run = await _make_run(db, organization_id=org.id, run_id=f"run-{uuid.uuid4().hex}")
    await _make_case(db, run=run, organization_id=org.id, case_id="c-1")
    await db.commit()

    stmt = (
        select(AIEvaluationRun)
        .options(selectinload(AIEvaluationRun.cases))
        .where(AIEvaluationRun.id == run.id)
    )
    reloaded = (await db.execute(stmt)).scalar_one()
    assert len(reloaded.cases) == 1
    assert reloaded.cases[0].case_id == "c-1"
    assert reloaded.cases[0].run_id == reloaded.id
    assert reloaded.cases[0].organization_id == org.id


@pytest.mark.asyncio
async def test_duplicate_run_id_rejected(db):
    org = await _make_org(db, "dup-run")
    run_id = f"run-{uuid.uuid4().hex}"
    first = await _make_run(db, organization_id=org.id, run_id=run_id)
    await db.commit()
    assert first.id is not None

    second = AIEvaluationRun(
        run_id=run_id,
        organization_id=org.id,
        target_type="rag",
        model="gpt-test",
    )
    db.add(second)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    rows = (
        (await db.execute(select(AIEvaluationRun).where(AIEvaluationRun.run_id == run_id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_duplicate_case_id_within_run_rejected(db):
    org = await _make_org(db, "dup-case")
    run = await _make_run(db, organization_id=org.id, run_id=f"run-{uuid.uuid4().hex}")
    await _make_case(db, run=run, organization_id=org.id, case_id="same")
    run_pk = run.id
    await db.commit()

    duplicate = AIEvaluationCase(
        run_id=run_pk,
        organization_id=org.id,
        case_id="same",
        case_type="agent",
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    rows = (
        (await db.execute(select(AIEvaluationCase).where(AIEvaluationCase.run_id == run_pk)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_different_tenants_remain_separate(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run_a = await _make_run(db, organization_id=org_a.id, run_id=f"run-a-{uuid.uuid4().hex}")
    run_b = await _make_run(db, organization_id=org_b.id, run_id=f"run-b-{uuid.uuid4().hex}")
    await db.commit()

    org_a_runs = (
        (
            await db.execute(
                select(AIEvaluationRun).where(AIEvaluationRun.organization_id == org_a.id)
            )
        )
        .scalars()
        .all()
    )
    org_b_runs = (
        (
            await db.execute(
                select(AIEvaluationRun).where(AIEvaluationRun.organization_id == org_b.id)
            )
        )
        .scalars()
        .all()
    )

    assert [r.id for r in org_a_runs] == [run_a.id]
    assert [r.id for r in org_b_runs] == [run_b.id]

    await _make_case(db, run=run_a, organization_id=org_a.id, case_id="c-1")
    await db.commit()
    org_a_cases = (
        (
            await db.execute(
                select(AIEvaluationCase).where(AIEvaluationCase.organization_id == org_a.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(org_a_cases) == 1
    org_b_cases = (
        (
            await db.execute(
                select(AIEvaluationCase).where(AIEvaluationCase.organization_id == org_b.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(org_b_cases) == 0


@pytest.mark.asyncio
async def test_cross_tenant_case_attachment_rejected(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run_a = await _make_run(db, organization_id=org_a.id, run_id=f"run-a-{uuid.uuid4().hex}")

    malformed = AIEvaluationCase(
        run_id=run_a.id,
        organization_id=org_b.id,
        case_id="stolen",
        case_type="agent",
    )
    db.add(malformed)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    rows = (
        (await db.execute(select(AIEvaluationCase).where(AIEvaluationCase.case_id == "stolen")))
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_baseline_history_retained(db):
    org = await _make_org(db, "baseline")
    for _ in range(2):
        db.add(
            AIEvaluationBaseline(
                organization_id=org.id,
                target_type="rag",
                version="1.0.0",
                model="gpt-test",
                pass_rate=0.95,
                metrics={},
                cases_count=5,
            )
        )
    await db.commit()

    rows = (
        (
            await db.execute(
                select(AIEvaluationBaseline).where(AIEvaluationBaseline.organization_id == org.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
