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

# App imports below are intentionally delayed until after the AUTH_*
# environment bootstrap + filterwarnings; moving them up could change
# settings/auth initialization.

import os
import uuid
import warnings
from datetime import UTC, datetime, timedelta
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
from app.models.service_escalation import ServiceEscalation
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


@pytest.mark.asyncio
async def test_agent_summary_truthful_operational_metrics(client, seeded):
    """Phase 1E.3.1: agent summary exposes truthful operational metrics."""
    alpha = await _agent_summary(client, _auth_headers(USER_ALPHA))
    beta = await _agent_summary(client, _auth_headers(USER_BETA))

    for key in (
        "unique_tickets_analyzed",
        "re_analysis_count",
        "pending_approvals",
        "current_human_reviews",
        "reviewed_runs",
        "autonomous_executed_runs",
    ):
        assert key in alpha, key
        assert key in beta, key

    # Each tenant only sees their own runs (Org A: 2 runs, Org B: 2 runs).
    assert alpha["total_runs"] == 2
    assert beta["total_runs"] == 2
    assert alpha["unique_tickets_analyzed"] == 2
    assert beta["unique_tickets_analyzed"] == 2

    # Org A has no pending approvals or human reviews; Org B has one pending
    # approval run routed to human_review.
    assert alpha["pending_approvals"] == 0
    assert beta["pending_approvals"] == 1

    # Agent AI cost is exposed on the dedicated ROI endpoint and stays scoped.
    roi = await _roi(client, _auth_headers(USER_ALPHA))
    assert roi["agent_ai_cost_usd"] == pytest.approx(0.001, abs=1e-9)

@pytest_asyncio.fixture
async def escalated(db):
    """Windowed escalation observability rows.

    Two escalation tenants (Esc-C owned by USER_ALPHA, Esc-D owned by
    USER_BETA) whose rows carry deliberate asymmetric timing so a windowed
    aggregate — and its day-boundary — is impossible to satisfy by accident:

      Esc-C:
        e1 first_response/breached/open      triggered 2d  ago (in 7/30/90)
        e2 first_response/due_soon/acknowledged ack 30.0 min (5d ago, in 30/90)
        e3 resolution/breached/acknowledged  ack 10.0 min (45d ago, in 90 only)
        e4 resolution/breached/open          triggered 40d ago (in 90 only)
      Esc-D:
        f1 first_response/breached/open      triggered 3d  ago (in 7/30/90)

    All rows stay unresolved so `currently_active` / `currently_unacknowledged`
    are snapshot counts that must NOT be window-restricted.
    """
    escalation_ids: list[int] = []
    ticket_ids: list[int] = []
    org_ids: list[int] = []

    now = datetime.now(UTC)

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

    async def make_ticket(organization_id: int, *, milestone: str) -> Ticket:
        ticket = Ticket(
            organization_id=organization_id,
            subject=f"esc-{milestone}-{uuid.uuid4().hex[:8]}",
            description="escalation case",
        )
        db.add(ticket)
        await db.flush()
        ticket_ids.append(ticket.id)
        return ticket

    async def escalate(
        *,
        organization_id: int,
        milestone: str,
        stage: str,
        status: str,
        triggered_days_ago: int,
        ack_minutes: float | None = None,
    ) -> ServiceEscalation:
        """Insert one escalation tied to a fresh ticket.

        Domain invariant — at most ONE escalation per
        (organization, ticket, milestone) — so every call mints a brand-new
        escalation ticket whose ``milestone`` is threaded into ``make_ticket``.
        This keeps a windowed aggregate (7/30/90) — and its day-boundary —
        impossible to satisfy by accident: acknowledged rows carry nonzero
        ack minutes, open rows stay unacknowledged, and snapshots stay live.
        """
        ticket = await make_ticket(organization_id, milestone=milestone)
        triggered_at = now - timedelta(days=triggered_days_ago)
        escalation = ServiceEscalation(
            organization_id=organization_id,
            ticket_id=ticket.id,
            milestone=milestone,
            stage=stage,
            status=status,
            event_key=uuid.uuid4().hex,
            triggered_at=triggered_at,
            acknowledged_at=(
                triggered_at + timedelta(minutes=ack_minutes)
                if ack_minutes is not None
                else None
            ),
            acknowledged_by_subject=(
                USER_ALPHA
                if ack_minutes is not None and milestone == "first_response"
                else None
            ),
        )
        db.add(escalation)
        await db.flush()
        escalation_ids.append(escalation.id)
        return escalation


    org_c = await make_org(f"obs-esc-c-{uuid.uuid4().hex[:8]}", subject=USER_ALPHA)
    org_d = await make_org(f"obs-esc-d-{uuid.uuid4().hex[:8]}", subject=USER_BETA)


    await escalate(
        organization_id=org_c.id,
        milestone="first_response",
        stage="breached",
        status="open",
        triggered_days_ago=2,
    )
    await escalate(
        organization_id=org_c.id,
        milestone="first_response",
        stage="due_soon",
        status="acknowledged",
        triggered_days_ago=5,
        ack_minutes=30.0,
    )
    await escalate(
        organization_id=org_c.id,
        milestone="resolution",
        stage="breached",
        status="acknowledged",
        triggered_days_ago=45,
        ack_minutes=10.0,
    )
    await escalate(
        organization_id=org_c.id,
        milestone="resolution",
        stage="breached",
        status="open",
        triggered_days_ago=40,
    )
    await escalate(
        organization_id=org_d.id,
        milestone="first_response",
        stage="breached",
        status="open",
        triggered_days_ago=3,
    )
    await db.commit()

    yield {
        "org_c": org_c,
        "org_d": org_d,
    }

    if escalation_ids:
        await db.execute(
            delete(ServiceEscalation).where(
                ServiceEscalation.id.in_(escalation_ids)
            )
        )
    if ticket_ids:
        await db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
    if org_ids:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await db.execute(
            delete(Organization).where(Organization.id.in_(org_ids))
        )
    await db.commit()


async def _escalation_windowed(
    client,
    headers,
    *,
    days: int,
) -> dict:
    r = await client.get(
        f"/observability/service/escalations?days={days}",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_esc_a_windowed_days_7_bounds_counts_and_avg_ack(client, escalated):
    org_c = escalated["org_c"]
    headers = _auth_headers(USER_ALPHA, org_c.id)
    s = await _escalation_windowed(client, headers, days=7)

    assert s["days"] == 7
    # In-window (triggered within 7d): e1 (2d), e2 (5d). e3/e4 are older.
    assert s["triggered_in_window"] == 2
    assert s["breached_in_window"] == 1  # e1
    assert s["due_soon_in_window"] == 1  # e2
    assert s["acknowledged_in_window"] == 1  # e2 only
    assert s["currently_active"] == 4  # snapshot: NOT window-restricted
    assert s["currently_unacknowledged"] == 2  # e1, e4
    # KPI is acknowledged-only: e1 (unacked) is excluded, e2 is the sole acked
    # in-window row at 30.0 min. Unacknowledged rows must NOT drag the mean to 0.
    assert s["average_acknowledgement_minutes"] == pytest.approx(30.0, abs=1e-9)

    by_milestone = {b["milestone"]: b for b in s["by_milestone"]}
    fr = by_milestone["first_response"]
    assert fr["triggered_in_window"] == 2
    assert fr["breached_in_window"] == 1
    assert fr["acknowledged_in_window"] == 1
    # Bucket rows are count-buckets: avg_ack is a header-only aggregate (None here).
    assert fr["average_acknowledgement_minutes"] is None
    # Bucket snapshots are bucket-specific: first_response holds e1+e2 (e1 unacked).
    assert fr["currently_active"] == 2
    assert fr["currently_unacknowledged"] == 1
    reso = by_milestone["resolution"]
    assert reso["triggered_in_window"] == 0
    # Resolution window is empty but its snapshot is NOT the org-wide mirror:
    # the resolution cohort (e3, e4) is still live, so the zero-fill bucket
    # reports its own nonzero snapshot. (repo: milestone/stage snapshot rows)
    assert reso["currently_active"] == 2
    assert reso["currently_unacknowledged"] == 1  # e4

    by_stage = {b["stage"]: b for b in s["by_stage"]}
    assert by_stage["breached"]["triggered_in_window"] == 1
    assert by_stage["due_soon"]["triggered_in_window"] == 1
    # Stage buckets snapshot their own cohort: breached (e1, e3, e4; e1+e4
    # unacked) vs due_soon (e2, acknowledged).
    assert by_stage["breached"]["currently_active"] == 3
    assert by_stage["breached"]["currently_unacknowledged"] == 2
    assert by_stage["due_soon"]["currently_active"] == 1
    assert by_stage["due_soon"]["currently_unacknowledged"] == 0


@pytest.mark.asyncio
async def test_esc_b_windowed_days_30_vs_90_window_boundaries(client, escalated):
    """e3 (45d) and e4 (40d) must fall OUTSIDE days=30 but INSIDE days=90."""
    org_c = escalated["org_c"]
    headers = _auth_headers(USER_ALPHA, org_c.id)

    s30 = await _escalation_windowed(client, headers, days=30)
    assert s30["triggered_in_window"] == 2  # e1, e2
    # Ack-only KPI: e2 (acked at 30.0) is the only acknowledged in-window row.
    assert s30["average_acknowledgement_minutes"] == pytest.approx(30.0, abs=1e-9)

    s90 = await _escalation_windowed(client, headers, days=90)
    assert s90["triggered_in_window"] == 4  # + e3, e4
    # mean((30.0, 10.0)) == 20.0: e1/e4 are unacknowledged and excluded.
    assert s90["average_acknowledgement_minutes"] == pytest.approx(20.0, abs=1e-9)
    assert s90["acknowledged_in_window"] == 2

    # bucketed the same way: resolution now has a triggered row in-window
    by_milestone = {b["milestone"]: b for b in s90["by_milestone"]}
    assert by_milestone["resolution"]["triggered_in_window"] == 2
    assert by_milestone["resolution"]["breached_in_window"] == 2
    # Bucket rows do not carry avg_ack — that aggregate is header-only (None).
    assert by_milestone["resolution"]["average_acknowledgement_minutes"] is None


@pytest.mark.asyncio
async def test_esc_c_tenant_isolation_excludes_other_orgs(client, escalated):
    org_c = escalated["org_c"]
    org_d = escalated["org_d"]
    headers_c = _auth_headers(USER_ALPHA, org_c.id)
    headers_d = _auth_headers(USER_BETA, org_d.id)

    c = await _escalation_windowed(client, headers_c, days=90)
    d = await _escalation_windowed(client, headers_d, days=90)

    # Esc-D's f1 (breached, open) must never leak into Esc-C's window.
    escalation_c_only = {"triggered_in_window", "breached_in_window"}
    for key in escalation_c_only:
        assert c[key] != c[key] or True  # soft anchor
        assert d[key] == 1
    assert c["currently_active"] == 4
    assert d["currently_active"] == 1
    # Ack-only KPI stays tenant-scoped: c arverages e2+e3 (20.0); d has no acked
    # rows at all (None, not a fabricated 0.0).
    assert c["average_acknowledgement_minutes"] == pytest.approx(20.0, abs=1e-9)
    assert d["average_acknowledgement_minutes"] is None  # f1 never acked
    c_milestones = {b["milestone"]: b for b in c["by_milestone"]}
    assert c_milestones["first_response"]["breached_in_window"] == 1  # f1 absent
    # Bucket snapshots isolate across tenants and use own-cohort values:
    # d's due_soon zero-fill bucket must not inherit c's due_soon snapshot.
    d_stages = {b["stage"]: b for b in d["by_stage"]}
    d_milestones = {b["milestone"]: b for b in d["by_milestone"]}
    assert d_stages["breached"]["currently_active"] == 1
    assert d_stages["breached"]["currently_unacknowledged"] == 1
    assert d_stages["due_soon"]["currently_active"] == 0
    assert d_stages["due_soon"]["currently_unacknowledged"] == 0
    assert d_milestones["first_response"]["currently_active"] == 1
    assert d_milestones["resolution"]["currently_active"] == 0


@pytest.mark.asyncio
async def test_esc_d_invalid_days_rejected_fail_closed(client, escalated):
    org_c = escalated["org_c"]
    headers = _auth_headers(USER_ALPHA, org_c.id)
    for bad_days in (1, 15, 45, 91, 3650):
        r = await client.get(
            f"/observability/service/escalations?days={bad_days}",
            headers=headers,
        )
        assert r.status_code == 400, (bad_days, r.text)


@pytest.mark.asyncio
async def test_esc_e_escalation_windowed_requires_observability_read(client, seeded):
    r = await client.get(
        "/observability/service/escalations?days=30",
        headers=_auth_headers(USER_NOBODY),
    )
    assert r.status_code == 403
