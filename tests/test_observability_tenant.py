"""Phase 1C.3D observability tenant-isolation tests.

Observability is tenant-owned data: every aggregate must be scoped to
``WHERE organization_id = :tenant_org`` in SQL, never aggregated globally then
filtered in Python. These tests pin that invariant with two synthetic
organizations whose AI usage/cost/latency values are deliberately asymmetric
(Org B carries the dramatic 9999/20998-dollar telemetry), so an accidental
global aggregation is impossible to miss.

Tenant resolution semantics reused from Phase 1C.1/1C.2/1C.3C:
- forged selector / no membership -> 403
- multi-membership without selector -> 409
- valid selector -> exactly that tenant's rows
"""

import os
import uuid
import warnings
from pathlib import Path

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-obs-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-obs-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

warnings.filterwarnings("ignore")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, func, select

from app.core.config import reset_settings_cache, settings
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
)
from app.models.ticket import Ticket

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-obs-issuer"
TEST_AUDIENCE = "test-obs-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"
USER_GAMMA = "user-gamma"
USER_NOBODY = "user-nobody"

X_TENANT = "X-CXOps-Organization-ID"

AUTO_NOTE = "Automatically approved by low-risk tool policy"

ORG_A_COST = 0.031  # 0.01 + 0.02 + 0.001
ORG_A_TOKENS = 4600  # 1500 + 3000 + 100
ORG_A_LATENCY = 150.0  # avg of 100, 300, 50
ORG_A_REQUESTS = 3  # 2 rag + 1 agent_decision
ORG_B_COST = 20998.0  # 9999 + 1000 + 9999


def _build_token(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "iss": TEST_ISSUER, "aud": TEST_AUDIENCE},
        TEST_SECRET,
        algorithm="HS256",
    )


def _auth_headers(sub: str, tenant_id: int | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_build_token(sub)}"}
    if tenant_id is not None:
        headers[X_TENANT] = str(tenant_id)
    return headers


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "hs256")
    monkeypatch.setenv("AUTH_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("AUTH_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("AUTH_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("AUTH_DEV_MODE", "False")
    monkeypatch.setenv("ENVIRONMENT", "development")
    reset_settings_cache()


@pytest.fixture
def client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def seeded(db):
    """Org A (user-alpha) and Org B (user-beta) with asymmetric telemetry.

    Org A: 2 rag requests (one success / one failure) + 1 agent_decision log;
           2 runs (1 autonomous-executed, 1 execution_failed); 2 jobs.
    Org B: 2 rag requests carrying the dramatic 9999/1000 dollar cost; 2 runs;
           2 jobs. NULL-org telemetry rows (legacy) are also seeded and must
           stay inert for every tenant dashboard.
    """
    org_ids: list[int] = []
    log_ids: list[int] = []
    run_ids: list[str] = []
    ticket_ids: list[int] = []
    job_ids: list[int] = []

    # Defensive: purge any leftover obs-* fixture orgs never torn down by an
    # aborting run (seeding error before yield skips the teardown block).
    leaked = await db.execute(
        select(Organization.id).where(Organization.name.like("obs-%"))
    )
    leaked_ids = [row[0] for row in leaked.all()]
    for tbl, col in [
        (AIRequestLog, "organization_id"),
        (AgentRun, "organization_id"),
        (Ticket, "organization_id"),
        (IntegrationJob, "organization_id"),
    ]:
        if leaked_ids:
            await db.execute(
                delete(tbl).where(getattr(tbl, col).in_(leaked_ids))
            )
    if leaked_ids:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(leaked_ids)
            )
        )
        await db.execute(
            delete(Organization).where(Organization.id.in_(leaked_ids))
        )
    await db.commit()

    async def make_org(name: str, *, subject: str) -> Organization:
        org = Organization(name=name)
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        db.add(
            OrganizationMembership(
                subject=subject,
                organization_id=org.id,
                role=OrganizationRole.OWNER,
            )
        )
        await db.commit()
        return org

    org_a = await make_org(f"obs-a-{uuid.uuid4().hex[:8]}", subject=USER_ALPHA)
    org_b = await make_org(f"obs-b-{uuid.uuid4().hex[:8]}", subject=USER_BETA)

    async def add_log(
        *,
        organization_id: int | None,
        feature: str,
        status: str,
        grounded: bool,
        latency_ms: float,
        total_tokens: int,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        request_id: str | None = None,
        error_message: str | None = None,
    ) -> AIRequestLog:
        log = AIRequestLog(
            organization_id=organization_id,
            request_id=request_id or uuid.uuid4().hex,
            feature=feature,
            model="test-model",
            status=status,
            question="q",
            answer="a",
            grounded=grounded,
            llm_called=True,
            retrieval_count=0,
            best_similarity=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=cost,
            latency_ms=latency_ms,
            sources=[],
            error_message=error_message,
        )
        db.add(log)
        await db.flush()
        log_ids.append(log.id)
        return log

    async def make_ticket(
        organization_id: int | None,
        *,
        subject: str,
    ):
        ticket = Ticket(
            organization_id=organization_id,
            subject=subject,
            description="support case",
        )
        db.add(ticket)
        await db.flush()
        ticket_ids.append(ticket.id)
        return ticket

    async def add_run(
        *,
        organization_id: int | None,
        action: str,
        status: str,
        reviewer_note: str | None = None,
        requires_human_approval: bool = False,
        tool_plan: list[dict] | None = None,
    ) -> AgentRun:
        ticket = await make_ticket(
            organization_id,
            subject=f"ticket-{uuid.uuid4().hex[:8]}",
        )
        run = AgentRun(
            run_id=uuid.uuid4().hex,
            ticket_id=ticket.id,
            organization_id=organization_id,
            action=action,
            reason="reason",
            response_draft="draft",
            reviewer_note=reviewer_note,
            status=status,
            requires_human_approval=requires_human_approval,
            sources=[],
            workflow_path=["load_ticket"],
            tool_plan=tool_plan or [],
        )
        db.add(run)
        await db.flush()
        run_ids.append(run.run_id)
        return run

    async def add_job(
        *,
        organization_id: int | None,
        job_type: str,
        status: str,
        attempts: int,
        max_attempts: int = 3,
    ) -> IntegrationJob:
        job = IntegrationJob(
            organization_id=organization_id,
            job_type=job_type,
            status=status,
            attempts=attempts,
            max_attempts=max_attempts,
            dedupe_key=uuid.uuid4().hex,
            payload={},
        )
        db.add(job)
        await db.flush()
        job_ids.append(job.id)
        return job

    # ---- Org A telemetry (cost 0.031, latency avg 150.0, tokens 4600) ----
    await add_log(
        organization_id=org_a.id,
        feature="rag_answer",
        status="success",
        grounded=True,
        latency_ms=100.0,
        total_tokens=1500,
        input_tokens=1000,
        output_tokens=500,
        cost=0.01,
    )
    await add_log(
        organization_id=org_a.id,
        feature="rag_answer",
        status="failure",
        grounded=False,
        latency_ms=300.0,
        total_tokens=3000,
        input_tokens=2000,
        output_tokens=1000,
        cost=0.02,
        error_message="boom",
    )

    run_a1 = await add_run(
        organization_id=org_a.id,
        action="respond",
        status="executed",
        reviewer_note=AUTO_NOTE,
        tool_plan=[
            {
                "tool": "zendesk.send_reply",
                "risk_level": "low",
                "authorized": True,
                "requires_approval": False,
            }
        ],
    )
    await add_run(
        organization_id=org_a.id,
        action="escalate",
        status="execution_failed",
        requires_human_approval=True,
    )
    await add_log(
        organization_id=org_a.id,
        feature="agent_decision",
        status="success",
        grounded=True,
        latency_ms=50.0,
        total_tokens=100,
        input_tokens=50,
        output_tokens=50,
        cost=0.001,
        request_id=f"agent-{run_a1.run_id}",
    )

    await add_job(
        organization_id=org_a.id,
        job_type="zendesk.sync_ticket",
        status="completed",
        attempts=1,
    )
    await add_job(
        organization_id=org_a.id,
        job_type="agent.execute",
        status="failed",
        attempts=4,
        max_attempts=3,
    )

    # ---- Org B telemetry (dramatically different: cost 20998) ----
    await add_log(
        organization_id=org_b.id,
        feature="rag_answer",
        status="success",
        grounded=True,
        latency_ms=50.0,
        total_tokens=500,
        input_tokens=250,
        output_tokens=250,
        cost=9999.0,
    )
    await add_log(
        organization_id=org_b.id,
        feature="rag_answer",
        status="success",
        grounded=False,
        latency_ms=50.0,
        total_tokens=500,
        input_tokens=250,
        output_tokens=250,
        cost=1000.0,
    )

    run_b1 = await add_run(
        organization_id=org_b.id,
        action="respond",
        status="executed",
        reviewer_note=AUTO_NOTE,
    )
    await add_run(
        organization_id=org_b.id,
        action="human_review",
        status="pending_approval",
        requires_human_approval=True,
    )
    await add_log(
        organization_id=org_b.id,
        feature="agent_decision",
        status="success",
        grounded=True,
        latency_ms=25.0,
        total_tokens=50,
        input_tokens=25,
        output_tokens=25,
        cost=9999.0,
        request_id=f"agent-{run_b1.run_id}",
    )

    await add_job(
        organization_id=org_b.id,
        job_type="zendesk.sync_ticket",
        status="completed",
        attempts=1,
    )
    await add_job(
        organization_id=org_b.id,
        job_type="agent.execute",
        status="failed",
        attempts=4,
        max_attempts=3,
    )

    # ---- Legacy NULL-org telemetry (inert; must enter no aggregate) ----
    await add_log(
        organization_id=None,
        feature="rag_answer",
        status="success",
        grounded=True,
        latency_ms=99999.0,
        total_tokens=999999,
        input_tokens=99999,
        output_tokens=99999,
        cost=9999.0,
    )
    await add_run(
        organization_id=None,
        action="respond",
        status="executed",
        reviewer_note=AUTO_NOTE,
    )
    await add_job(
        organization_id=None,
        job_type="zendesk.sync_ticket",
        status="completed",
        attempts=1,
    )

    # multi-membership subject (user-gamma, member of Org A AND Org B) for
    # selector tests: without a selector -> 409; with a valid selector the
    # dashboard must switch exactly to the selected tenant.
    gamma = await make_org(f"obs-gamma-{uuid.uuid4().hex[:8]}", subject=USER_GAMMA)
    db.add(
        OrganizationMembership(
            subject=USER_GAMMA,
            organization_id=org_a.id,
            role=OrganizationRole.OWNER,
        )
    )
    db.add(
        OrganizationMembership(
            subject=USER_GAMMA,
            organization_id=org_b.id,
            role=OrganizationRole.OWNER,
        )
    )
    await db.commit()

    yield {
        "org_a": org_a,
        "org_b": org_b,
        "gamma": gamma,
        "log_ids": log_ids,
        "run_ids": run_ids,
        "job_ids": job_ids,
        "ticket_ids": ticket_ids,
    }

    # Broadcast teardown: telemetry/jobs/runs first, then memberships, orgs.
    if log_ids:
        await db.execute(
            delete(AIRequestLog).where(AIRequestLog.id.in_(log_ids))
        )
    if run_ids:
        await db.execute(delete(AgentRun).where(AgentRun.run_id.in_(run_ids)))
    if job_ids:
        await db.execute(
            delete(IntegrationJob).where(IntegrationJob.id.in_(job_ids))
        )
    if ticket_ids:
        await db.execute(
            delete(Ticket).where(Ticket.id.in_(ticket_ids))
        )
    if org_ids:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


async def _ai_summary(client, headers) -> dict:
    r = await client.get("/observability/ai/summary", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _agent_summary(client, headers) -> dict:
    r = await client.get("/observability/agent/summary", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _by_feature(client, headers) -> dict:
    r = await client.get("/observability/ai/by-feature", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _roi(client, headers) -> dict:
    r = await client.get("/observability/agent/roi", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_a_org_a_summary_counts_only_org_a(client, seeded):
    summary = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary["total_requests"] == ORG_A_REQUESTS  # 3, not 6


@pytest.mark.asyncio
async def test_b_org_a_token_totals_exclude_org_b(client, seeded):
    summary = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary["total_tokens"] == ORG_A_TOKENS  # 4600, not 5650


@pytest.mark.asyncio
async def test_c_org_a_estimated_cost_excludes_org_b(client, seeded):
    summary = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary["estimated_cost_usd"] == pytest.approx(ORG_A_COST, abs=1e-9)
    assert summary["estimated_cost_usd"] != pytest.approx(
        ORG_A_COST + ORG_B_COST,
        abs=1e-9,
    )


@pytest.mark.asyncio
async def test_d_org_a_latency_excludes_org_b(client, seeded):
    summary = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary["avg_latency_ms"] == pytest.approx(ORG_A_LATENCY, abs=1e-9)


@pytest.mark.asyncio
async def test_e_org_a_action_counts_exclude_org_b(client, seeded):
    summary = await _agent_summary(client, _auth_headers(USER_ALPHA))
    assert summary["actions"] == {"respond": 1, "escalate": 1}
    assert summary["total_runs"] == 2


@pytest.mark.asyncio
async def test_f_org_a_success_failure_rates_exclude_org_b(client, seeded):
    summary = await _agent_summary(client, _auth_headers(USER_ALPHA))
    assert summary["statuses"] == {"executed": 1, "execution_failed": 1}
    assert summary["executed_runs"] == 1
    assert summary["execution_failed_runs"] == 1
    assert summary["execution_success_rate"] == 50.0
    summary_ai = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary_ai["success_rate"] == pytest.approx(2 / 3)
    assert summary_ai["grounded_rate"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_gh_roi_numerator_and_denominator_exclude_org_b(client, seeded):
    roi = await _roi(client, _auth_headers(USER_ALPHA))
    assert roi["total_runs"] == 2  # denominator is Org A only
    assert roi["instrumented_runs"] == 1  # numerator is Org A only
    assert roi["instrumented_autonomous_executed_runs"] == 1
    assert roi["agent_ai_cost_usd"] == pytest.approx(0.001, abs=1e-9)
    assert roi["estimated_minutes_saved"] == pytest.approx(
        settings.minutes_saved_per_autonomous_execution
    )


@pytest.mark.asyncio
async def test_i_by_feature_breakdown_contains_only_org_a(client, seeded):
    breakdown = await _by_feature(client, _auth_headers(USER_ALPHA))
    overall = breakdown["overall"]
    assert overall["total_requests"] == ORG_A_REQUESTS
    assert overall["total_tokens"] == ORG_A_TOKENS
    by_feature = {f["feature"]: f for f in breakdown["features"]}
    assert by_feature["rag_answer"]["total_requests"] == 2
    assert by_feature["rag_answer"]["total_tokens"] == 4500
    assert by_feature["rag_answer"]["avg_latency_ms"] == pytest.approx(200.0)
    assert by_feature["rag_answer"]["estimated_cost_usd"] == pytest.approx(0.03)
    assert by_feature["agent_decision"]["total_requests"] == 1


@pytest.mark.asyncio
async def test_j_forged_tenant_selector_forbidden(client, seeded):
    org_b = seeded["org_b"]
    r = await client.get(
        "/observability/ai/summary",
        headers=_auth_headers(USER_ALPHA, org_b.id),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_k_no_membership_forbidden(client, seeded):
    r = await client.get(
        "/observability/ai/summary",
        headers=_auth_headers(USER_NOBODY),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_l_multi_membership_without_selector_conflict(client, seeded):
    r = await client.get(
        "/observability/ai/summary",
        headers=_auth_headers(USER_GAMMA),
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_m_valid_selector_switches_to_selected_tenant(client, seeded):
    org_a, org_b = seeded["org_a"], seeded["org_b"]

    a = await _ai_summary(client, _auth_headers(USER_GAMMA, org_a.id))
    assert a["total_requests"] == ORG_A_REQUESTS
    assert a["estimated_cost_usd"] == pytest.approx(ORG_A_COST, abs=1e-9)

    b = await _ai_summary(client, _auth_headers(USER_GAMMA, org_b.id))
    assert b["total_requests"] == ORG_A_REQUESTS
    assert b["estimated_cost_usd"] == pytest.approx(ORG_B_COST, abs=1e-9)
    assert b["estimated_cost_usd"] != a["estimated_cost_usd"]


@pytest.mark.asyncio
async def test_n_legacy_null_org_telemetry_inert(client, seeded):
    summary = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary["total_requests"] == ORG_A_REQUESTS
    assert summary["total_tokens"] == ORG_A_TOKENS
    assert summary["estimated_cost_usd"] == pytest.approx(ORG_A_COST, abs=1e-9)
    assert summary["avg_latency_ms"] == pytest.approx(ORG_A_LATENCY, abs=1e-9)

    agent = await _agent_summary(client, _auth_headers(USER_ALPHA))
    assert agent["total_runs"] == 2
    assert agent["integration_jobs"]["total"] == 2


@pytest.mark.asyncio
async def test_o_no_detail_drilldown_surface(client, seeded):
    for path in (
        "/observability/requests/999",
        "/observability/runs/999",
        "/observability/ai/summary/999",
        "/observability/agent/roi/999",
    ):
        r = await client.get(path, headers=_auth_headers(USER_ALPHA))
        assert r.status_code == 404, path


@pytest.mark.asyncio
async def test_p_no_unscoped_observability_aggregation_callers():
    root = Path(__file__).resolve().parent.parent
    supervised = [
        root / "app" / "api" / "routes" / "observability.py",
        root / "app" / "services" / "ai_observability_service.py",
        root / "app" / "services" / "agent_observability_service.py",
    ]
    for path in supervised:
        source = path.read_text()
        assert "_unscoped" not in source, f"{path.name} must not call *_unscoped"
        # Every aggregation must reference organization_id (tenant predicate).
        assert "organization_id" in source, (
            f"{path.name} must always scope by organization_id"
        )


@pytest.mark.asyncio
async def test_q_organization_predicate_removal_breaks_aggregation(client, seeded):
    a = await _ai_summary(client, _auth_headers(USER_ALPHA))

    # A global (predicate-free) count would include Org B + NULL-org rows.
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(func.count(AIRequestLog.id)))
        global_count = int(result.scalar_one())

    assert a["total_requests"] != global_count
    assert a["estimated_cost_usd"] != pytest.approx(
        ORG_A_COST + ORG_B_COST,
        abs=1e-9,
    )


@pytest.mark.asyncio
async def test_aggregation_leak_regression_cost(client, seeded):
    """Org A cost 0.031 vs Org B cost 20998: Org A must see exactly 0.031.

    Removing the organization_id predicate would surface either the raw
    global sum (ORG_A_COST + ORG_B_COST) or the Org B value (20998.0) here.
    """
    summary = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary["estimated_cost_usd"] == pytest.approx(ORG_A_COST, abs=1e-9)
    assert summary["estimated_cost_usd"] != pytest.approx(ORG_B_COST, abs=1e-9)
    assert summary["estimated_cost_usd"] != pytest.approx(
        ORG_A_COST + ORG_B_COST,
        abs=1e-9,
    )


@pytest.mark.asyncio
async def test_aggregation_leak_regression_counts_and_latency(client, seeded):
    """Count + latency analogs of the leak regression."""
    summary = await _ai_summary(client, _auth_headers(USER_ALPHA))
    assert summary["total_requests"] == ORG_A_REQUESTS  # global would be 6
    assert summary["total_tokens"] == ORG_A_TOKENS  # global would be 5650
    assert summary["avg_latency_ms"] == pytest.approx(
        ORG_A_LATENCY,
        abs=1e-9,
    )  # global average would be ~95.83

    agent = await _agent_summary(client, _auth_headers(USER_ALPHA))
    assert agent["total_runs"] == 2  # global would be 5 (incl. NULL-org)
    assert agent["actions"] == {"respond": 1, "escalate": 1}  # global: respond 3


@pytest.mark.asyncio
async def test_aggregation_leak_regression_roi(client, seeded):
    """ROI denominator + numerator leak regression."""
    roi = await _roi(client, _auth_headers(USER_ALPHA))
    assert roi["total_runs"] == 2  # global would be 5
    assert roi["instrumented_runs"] == 1  # global would be 3
    assert roi["agent_ai_cost_usd"] == pytest.approx(0.001, abs=1e-9)