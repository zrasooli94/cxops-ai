"""Phase 1J.1B — evaluation repository tests.

Covers the required tenant-safety matrix:

A. create + fetch run in tenant A
B. tenant B cannot fetch tenant A run
C. run list contains only current tenant
D. create case under same-tenant run succeeds
E. cross-tenant case attachment fails safely
F. case list is tenant-scoped
G. create + list baselines by tenant
H. latest baseline returns correct tenant baseline
I. pagination is bounded/deterministic
"""

import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization
from app.repositories.ai_evaluation_repository import AIEvaluationRepository


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _make_org(db, tag: str) -> Organization:
    org = Organization(name=f"eval-repo-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    return org


def _run_id(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex}"


async def _make_run(db, *, organization_id: int, tag: str = "run"):
    return await AIEvaluationRepository.create_run(
        db,
        run_id=_run_id(tag),
        organization_id=organization_id,
        target_type="agent",
        model="gpt-test",
        agent_decision_version="2",
        tool_policy_version=1,
    )


# ---------------------------------------------------------------- runs: A, B, C


@pytest.mark.asyncio
async def test_create_and_fetch_run_for_tenant(db):
    org = await _make_org(db, "a")
    run = await _make_run(db, organization_id=org.id)

    got = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run.run_id, organization_id=org.id
    )
    assert got is not None
    assert got.id == run.id
    assert got.organization_id == org.id
    assert got.status == "queued"
    assert got.trigger_source == "manual"
    assert got.metrics == {}
    assert got.target_type == "agent"


@pytest.mark.asyncio
async def test_foreign_tenant_run_not_returned(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run_a = await _make_run(db, organization_id=org_a.id)

    got = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run_a.run_id, organization_id=org_b.id
    )
    assert got is None


@pytest.mark.asyncio
async def test_run_list_contains_only_current_tenant(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run_a1 = await _make_run(db, organization_id=org_a.id, tag="a1")
    run_a2 = await _make_run(db, organization_id=org_a.id, tag="a2")
    run_b = await _make_run(db, organization_id=org_b.id, tag="b")

    runs_a = await AIEvaluationRepository.list_runs_for_tenant(db, organization_id=org_a.id)
    run_ids_a = {r.run_id for r in runs_a}
    assert run_ids_a == {run_a1.run_id, run_a2.run_id}
    assert run_b.run_id not in run_ids_a

    runs_b = await AIEvaluationRepository.list_runs_for_tenant(db, organization_id=org_b.id)
    assert [r.run_id for r in runs_b] == [run_b.run_id]

    total_a = await AIEvaluationRepository.count_runs_for_tenant(db, organization_id=org_a.id)
    assert total_a == 2


# --------------------------------------------------------------- cases: D, E, F


@pytest.mark.asyncio
async def test_add_case_under_same_tenant_run_succeeds(db):
    org = await _make_org(db, "a")
    run = await _make_run(db, organization_id=org.id)

    case = await AIEvaluationRepository.add_case(
        db,
        run_id=run.run_id,
        organization_id=org.id,
        case_id="c-1",
        case_type="agent",
        expected={"expected_action": "route"},
        actual={"actual_action": "route"},
        dimensions={"action_pass": True},
        input_data={"ticket_subject": "refund"},
        latency_ms=120.5,
        total_tokens=310,
        estimated_cost_usd=0.004,
        fingerprint="fp-1",
    )
    assert case is not None
    assert case.run_id == run.id
    assert case.organization_id == org.id
    assert case.case_id == "c-1"
    assert case.expected == {"expected_action": "route"}
    assert case.dimensions == {"action_pass": True}


@pytest.mark.asyncio
async def test_cross_tenant_case_attachment_fails_safely(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run_a = await _make_run(db, organization_id=org_a.id)

    case = await AIEvaluationRepository.add_case(
        db,
        run_id=run_a.run_id,
        organization_id=org_b.id,
        case_id="stolen",
        case_type="agent",
    )
    assert case is None

    cases = await AIEvaluationRepository.list_cases_for_run_for_tenant(
        db, run_id=run_a.run_id, organization_id=org_a.id
    )
    assert cases == []


@pytest.mark.asyncio
async def test_case_list_is_tenant_scoped(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run_a = await _make_run(db, organization_id=org_a.id, tag="a")
    run_b = await _make_run(db, organization_id=org_b.id, tag="b")

    await AIEvaluationRepository.add_case(
        db, run_id=run_a.run_id, organization_id=org_a.id, case_id="c-1", case_type="agent"
    )
    await AIEvaluationRepository.add_case(
        db, run_id=run_a.run_id, organization_id=org_a.id, case_id="c-2", case_type="agent"
    )
    await AIEvaluationRepository.add_case(
        db, run_id=run_b.run_id, organization_id=org_b.id, case_id="c-3", case_type="agent"
    )

    cases_a = await AIEvaluationRepository.list_cases_for_run_for_tenant(
        db, run_id=run_a.run_id, organization_id=org_a.id
    )
    assert [c.case_id for c in cases_a] == ["c-1", "c-2"]

    cases_a_from_b = await AIEvaluationRepository.list_cases_for_run_for_tenant(
        db, run_id=run_a.run_id, organization_id=org_b.id
    )
    assert cases_a_from_b == []

    cases_b = await AIEvaluationRepository.list_cases_for_run_for_tenant(
        db, run_id=run_b.run_id, organization_id=org_b.id
    )
    assert [c.case_id for c in cases_b] == ["c-3"]


# ------------------------------------------------------------ baselines: G, H


@pytest.mark.asyncio
async def test_create_and_list_baselines_by_tenant(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")

    await AIEvaluationRepository.create_baseline(
        db,
        organization_id=org_a.id,
        target_type="rag",
        version="1.0.0",
        model="gpt-test",
        pass_rate=0.9,
        cases_count=5,
    )
    await AIEvaluationRepository.create_baseline(
        db,
        organization_id=org_a.id,
        target_type="agent",
        version="1.1.0",
        model="gpt-test",
        pass_rate=0.8,
        cases_count=3,
    )
    await AIEvaluationRepository.create_baseline(
        db,
        organization_id=org_b.id,
        target_type="rag",
        version="9.0.0",
        model="gpt-test",
        pass_rate=0.5,
        cases_count=1,
    )

    all_a = await AIEvaluationRepository.list_baselines_for_tenant(db, organization_id=org_a.id)
    assert sorted(b.target_type for b in all_a) == ["agent", "rag"]

    rag_a = await AIEvaluationRepository.list_baselines_for_tenant(
        db, organization_id=org_a.id, target_type="rag"
    )
    assert len(rag_a) == 1
    assert rag_a[0].version == "1.0.0"


@pytest.mark.asyncio
async def test_latest_baseline_is_tenant_scoped_and_stable(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")

    await AIEvaluationRepository.create_baseline(
        db,
        organization_id=org_a.id,
        target_type="rag",
        version="1.0.0",
        model="gpt-test",
        pass_rate=0.85,
        cases_count=10,
    )
    b2 = await AIEvaluationRepository.create_baseline(
        db,
        organization_id=org_a.id,
        target_type="rag",
        version="1.1.0",
        model="gpt-test",
        pass_rate=0.92,
        cases_count=12,
    )
    b_other = await AIEvaluationRepository.create_baseline(
        db,
        organization_id=org_b.id,
        target_type="rag",
        version="0.1.0",
        model="gpt-test",
        pass_rate=0.4,
        cases_count=2,
    )

    latest_a = await AIEvaluationRepository.get_latest_baseline_for_tenant(
        db, organization_id=org_a.id, target_type="rag"
    )
    assert latest_a is not None
    assert latest_a.version == "1.1.0"
    assert latest_a.id == b2.id

    latest_b = await AIEvaluationRepository.get_latest_baseline_for_tenant(
        db, organization_id=org_b.id, target_type="rag"
    )
    assert latest_b is not None
    assert latest_b.id == b_other.id
    assert latest_b.id != b2.id


# ------------------------------------------------------------ pagination: I


@pytest.mark.asyncio
async def test_run_listing_pagination_is_bounded_and_deterministic(db):
    org = await _make_org(db, "a")
    ids = []
    for index in range(5):
        run = await _make_run(db, organization_id=org.id, tag=f"page-{index}")
        ids.append(run.id)

    ids_desc = sorted(ids, reverse=True)

    page1 = await AIEvaluationRepository.list_runs_for_tenant(
        db, organization_id=org.id, limit=2, offset=0
    )
    assert [r.id for r in page1] == ids_desc[:2]

    page2 = await AIEvaluationRepository.list_runs_for_tenant(
        db, organization_id=org.id, limit=2, offset=2
    )
    assert [r.id for r in page2] == ids_desc[2:4]

    page3 = await AIEvaluationRepository.list_runs_for_tenant(
        db, organization_id=org.id, limit=2, offset=4
    )
    assert [r.id for r in page3] == ids_desc[4:]

    combined = [r.id for r in page1] + [r.id for r in page2] + [r.id for r in page3]
    assert combined == ids_desc
    assert len(combined) == 5

    huge = await AIEvaluationRepository.list_runs_for_tenant(
        db, organization_id=org.id, limit=10**6, offset=-50
    )
    assert len(huge) == 5


# ------------------------------------------------------------ update: tenant-safe


@pytest.mark.asyncio
async def test_update_run_is_tenant_safe_and_allowlisted(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    run = await _make_run(db, organization_id=org_a.id)

    updated = await AIEvaluationRepository.update_run(
        db,
        run_id=run.run_id,
        organization_id=org_a.id,
        values={"status": "succeeded", "pass_rate": 0.95, "metrics": {"action": 0.9}},
    )
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.pass_rate == 0.95

    untouched = await AIEvaluationRepository.update_run(
        db,
        run_id=run.run_id,
        organization_id=org_b.id,
        values={"status": "failed"},
    )
    assert untouched is None

    reloaded = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run.run_id, organization_id=org_a.id
    )
    assert reloaded is not None
    assert reloaded.status == "succeeded"

    with pytest.raises(ValueError):
        await AIEvaluationRepository.update_run(
            db,
            run_id=run.run_id,
            organization_id=org_a.id,
            values={"organization_id": org_b.id},
        )
