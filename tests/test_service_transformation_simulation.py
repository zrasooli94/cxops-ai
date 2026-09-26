"""Phase 1N service transformation simulation tests.

Covers the scenario model/migration constraints, the strict 0-100 assumption
contract, the pure deterministic engine (formulas, clamps, bounded warnings,
no baseline mutation), the value/ROI projection that inherits Phase 1L
semantics and its three measurement states, tenant isolation, RBAC gates, the
API lifecycle (create/list/get/evaluate/archive), and the persisted-baseline
snapshot captured from the existing Phase 1L summary.
"""

# App imports are delayed until after the AUTH_* environment bootstrap +
# warnings filter: the app reads its settings at import time, so the app
# import block below carries an explicit E402 waiver (repo-wide bootstrap
# convention).

import os
import uuid
import warnings
from datetime import UTC, datetime, timedelta
from math import inf, nan
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-xtf-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-xtf-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

warnings.filterwarnings("ignore")

from app.core.config import reset_settings_cache, settings
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
)
from app.models.service_transformation_scenario import (
    VALID_SCENARIO_STATUSES,
    VALID_SCENARIO_WINDOWS,
    ServiceTransformationScenario,
)
from app.models.ticket import Ticket
from app.schemas.service_transformation_simulation import (
    ServiceTransformationScenarioCreate,
    SimulationAssumptions,
)
from app.services.service_transformation_simulation_engine import (
    MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE,
    MEASUREMENT_STATUS_MEASURED,
    MEASUREMENT_STATUS_PRICING_UNAVAILABLE,
    SERVICE_TRANSFORMATION_SIMULATION_VERSION,
    simulate,
)

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-xtf-issuer"
TEST_AUDIENCE = "test-xtf-audience"

USER_SIMA = "user-sima"
USER_SIMB = "user-simb"
USER_SUPER = "user-super"
USER_VIEWER = "user-viewer"
USER_AGENT = "user-agent"
USER_NOBODY = "user-nobody"

X_TENANT = "X-CXOps-Organization-ID"

AUTO_NOTE = "Automatically approved by low-risk tool policy"

REPO_ROOT = Path(__file__).resolve().parent.parent


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


def _scenario_payload(
    *,
    name: str = "Raise autonomous coverage",
    days: int = 30,
    autonomous: float | None = 60.0,
    description: str | None = "Scenario description",
) -> dict:
    assumptions = {
        "autonomous_execution_rate_target": autonomous,
        "human_approval_rate_target": None,
        "knowledge_usage_rate_target": None,
        "reopen_rate_target": None,
        "sla_breach_reduction_percent": None,
    }
    payload = {
        "name": name,
        "days": days,
        "assumptions": assumptions,
    }
    if description is not None:
        payload["description"] = description
    return payload


@pytest_asyncio.fixture
async def seeded(db):
    """Two kept-apart tenants plus a small Org A agent baseline.

    Org A has three agent runs (two instrumented autonomous/executed with
    telemetry, one plain) so ``agent_runs=3`` and the value baseline reports
    ``insufficient_sample`` (2 < roi_min_autonomous_samples=20). Org B is a
    separate owner-only tenant that must never see Org A rows.
    """
    org_ids: list[int] = []

    leaked = await db.execute(
        select(Organization.id).where(Organization.name.like("xtf-%"))
    )
    leaked_ids = [row[0] for row in leaked.all()]
    for tbl, col in [
        (ServiceTransformationScenario, "organization_id"),
        (AIRequestLog, "organization_id"),
        (AgentRun, "organization_id"),
        (Ticket, "organization_id"),
    ]:
        if leaked_ids:
            await db.execute(delete(tbl).where(getattr(tbl, col).in_(leaked_ids)))
    if leaked_ids:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(leaked_ids)
            )
        )
        await db.execute(delete(Organization).where(Organization.id.in_(leaked_ids)))
    await db.commit()

    now = datetime.now(UTC)

    async def make_org(name: str, *, subject: str, role: OrganizationRole) -> Organization:
        org = Organization(name=name)
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        db.add(
            OrganizationMembership(
                subject=subject,
                organization_id=org.id,
                role=role,
            )
        )
        return org

    org_a = await make_org(
        f"xtf-a-{uuid.uuid4().hex[:8]}", subject=USER_SIMA, role=OrganizationRole.OWNER
    )
    org_b = await make_org(
        f"xtf-b-{uuid.uuid4().hex[:8]}", subject=USER_SIMB, role=OrganizationRole.OWNER
    )

    db.add(
        OrganizationMembership(
            subject=USER_SUPER,
            organization_id=org_a.id,
            role=OrganizationRole.SUPERVISOR,
        )
    )
    db.add(
        OrganizationMembership(
            subject=USER_VIEWER,
            organization_id=org_a.id,
            role=OrganizationRole.VIEWER,
        )
    )
    db.add(
        OrganizationMembership(
            subject=USER_AGENT,
            organization_id=org_a.id,
            role=OrganizationRole.AGENT,
        )
    )
    await db.commit()

    ticket = Ticket(
        organization_id=org_a.id,
        subject=f"sim-{uuid.uuid4().hex[:8]}",
        description="support case",
        status="open",
        priority="normal",
        created_at=now - timedelta(days=1),
        resolved_at=None,
        first_response_at=now - timedelta(days=1),
        first_response_due_at=now,
    )
    db.add(ticket)
    await db.flush()

    for i in range(3):
        run = AgentRun(
            run_id=uuid.uuid4().hex,
            ticket_id=ticket.id,
            organization_id=org_a.id,
            action="suggest",
            reason="reason",
            response_draft="draft",
            status="executed",
            sources=[],
            workflow_path=["coordinator", "knowledge"],
            tool_plan=[],
            authorization_source="policy_auto",
            reviewer_note=AUTO_NOTE,
            created_at=now - timedelta(days=1),
        )
        db.add(run)
        await db.flush()
        if i < 2:
            db.add(
                AIRequestLog(
                    organization_id=org_a.id,
                    request_id=f"agent-{run.run_id}",
                    feature="agent_decision",
                    model="test-model",
                    status="success",
                    question="q",
                    answer="a",
                    grounded=True,
                    llm_called=True,
                    retrieval_count=0,
                    best_similarity=None,
                    input_tokens=100,
                    output_tokens=100,
                    total_tokens=200,
                    estimated_cost_usd=0.01,
                    latency_ms=10.0,
                    sources=[],
                    error_message=None,
                )
            )
    await db.commit()
    return {"org_a": org_a, "org_b": org_b}


def _value_baseline(
    *,
    minutes: float = 400.0,
    labor: float = 166.67,
    cost: float = 40.0,
    pricing_configured: bool = True,
    sample_size_sufficient: bool = True,
    status: str = "measured",
    roi_percent: float | None = 316.67,
) -> dict:
    return {
        "estimated_minutes_saved": minutes,
        "estimated_hours_saved": round(minutes / 60, 2),
        "estimated_labor_savings_usd": labor,
        "agent_ai_cost_usd": cost,
        "estimated_net_savings_usd": round(labor - cost, 6),
        "pricing_configured": pricing_configured,
        "measurement_status": status,
        "minimum_autonomous_samples": settings.roi_min_autonomous_samples,
        "sample_size_sufficient": sample_size_sufficient,
        "roi_percent": roi_percent,
    }


def _baseline(
    *,
    agent_runs: int = 100,
    autonomous: int = 40,
    human_approval: int = 30,
    knowledge_runs: int = 20,
    reopened: int = 10,
    resolved: int = 200,
    breaches: int = 120,
    value: dict | None = None,
) -> dict:
    return {
        "agent_runs": agent_runs,
        "autonomous_executions": autonomous,
        "human_approval_required": human_approval,
        "knowledge_specialist_runs": knowledge_runs,
        "reopened_tickets": reopened,
        "tickets_resolved": resolved,
        "total_sla_breaches": breaches,
        "rates": {
            "autonomous_execution_rate": (
                round(autonomous / agent_runs * 100, 2) if agent_runs > 0 else None
            ),
            "human_approval_rate": (
                round(human_approval / agent_runs * 100, 2) if agent_runs > 0 else None
            ),
            "knowledge_usage_rate": (
                round(knowledge_runs / agent_runs * 100, 2) if agent_runs > 0 else None
            ),
            "reopen_rate": (
                round(reopened / resolved * 100, 2) if resolved > 0 else None
            ),
        },
        "value": value if value is not None else _value_baseline(),
    }


# ---------------------------------------------------------------------------
# Model / migration
# ---------------------------------------------------------------------------


def test_alembic_single_head():
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads
    assert heads[0] == "c3f9a1d2b7e4"


def test_scenario_model_org_not_null_and_constraints():
    column = ServiceTransformationScenario.__table__.c.organization_id
    assert column.nullable is False
    ck_names = {
        c.name
        for c in ServiceTransformationScenario.__table__.constraints
        if c.name and c.name.startswith("ck_")
    }
    assert "ck_service_transformation_scenarios_window_days" in ck_names
    assert "ck_service_transformation_scenarios_status" in ck_names
    index_cols = {
        i.name: {c.name for c in i.columns}
        for i in ServiceTransformationScenario.__table__.indexes
    }
    assert "ix_service_transformation_scenarios_organization_id" in index_cols
    assert index_cols["ix_service_transformation_scenarios_organization_id"] == {
        "organization_id"
    }
    assert VALID_SCENARIO_WINDOWS == (7, 30, 90)
    assert VALID_SCENARIO_STATUSES == ("draft", "evaluated", "archived")


# ---------------------------------------------------------------------------
# Assumption contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [-0.1, 100.1, 200, -1])
def test_assumptions_reject_out_of_range(bad_value):
    with pytest.raises(ValueError):
        SimulationAssumptions(autonomous_execution_rate_target=bad_value)


@pytest.mark.parametrize("bad_value", [nan, inf, -inf, float("inf")])
def test_assumptions_reject_non_finite(bad_value):
    with pytest.raises(ValueError):
        SimulationAssumptions(autonomous_execution_rate_target=bad_value)


def test_assumptions_reject_unknown_key():
    with pytest.raises(ValueError):
        SimulationAssumptions(  # type: ignore[call-arg]
            autonomous_execution_rate_target=1.0,
            made_up_target=50.0,
        )


def test_assumptions_reject_empty():
    with pytest.raises(ValueError):
        SimulationAssumptions()


def test_assumptions_accepts_fractional_single_dimension():
    a = SimulationAssumptions(sla_breach_reduction_percent=12.5)
    assert a.sla_breach_reduction_percent == 12.5


def test_create_rejects_org_id_in_body():
    with pytest.raises(ValueError):
        ServiceTransformationScenarioCreate(
            name="x",
            days=30,
            assumptions={"autonomous_execution_rate_target": 50.0},
            organization_id=42,  # type: ignore[call-arg]
        )


def test_create_rejects_unsupported_days_and_blank_name():
    with pytest.raises(ValueError):
        ServiceTransformationScenarioCreate(
            name="x",
            days=15,
            assumptions={"autonomous_execution_rate_target": 50.0},
        )
    with pytest.raises(ValueError):
        ServiceTransformationScenarioCreate(
            name="   ",
            days=30,
            assumptions={"autonomous_execution_rate_target": 50.0},
        )


# ---------------------------------------------------------------------------
# Pure engine
# ---------------------------------------------------------------------------


def test_engine_deterministic():
    a = simulate(_baseline(), {"autonomous_execution_rate_target": 60.0})
    b = simulate(_baseline(), {"autonomous_execution_rate_target": 60.0})
    assert a == b


def test_engine_does_not_mutate_baseline():
    baseline = _baseline()
    before = dict(baseline)
    simulate(baseline, {"autonomous_execution_rate_target": 60.0})
    assert baseline == before


def test_engine_autonomous_formula_and_clamp():
    result = simulate(
        _baseline(agent_runs=100, autonomous=40),
        {"autonomous_execution_rate_target": 60.0},
    )
    p = result["projected"]
    assert p["autonomous_executions"] == 60
    assert p["autonomous_execution_rate_percent"] == 60.0
    assert result["deltas"]["autonomous_executions"] == 20
    assert any("eligibility is not modelled" in w for w in result["warnings"])

    cap = simulate(
        _baseline(agent_runs=100, autonomous=40),
        {"autonomous_execution_rate_target": 100.0},
    )
    assert cap["projected"]["autonomous_executions"] == 100


def test_engine_human_approval_formula():
    result = simulate(
        _baseline(agent_runs=100, human_approval=30),
        {"human_approval_rate_target": 25.0},
    )
    p = result["projected"]
    assert p["human_approval_required"] == 25
    assert p["human_approval_rate_percent"] == 25.0
    assert result["deltas"]["human_approval_required"] == -5


def test_engine_knowledge_formula():
    result = simulate(
        _baseline(agent_runs=100, knowledge_runs=20),
        {"knowledge_usage_rate_target": 50.0},
    )
    assert result["projected"]["knowledge_specialist_runs"] == 50
    assert result["projected"]["knowledge_usage_rate_percent"] == 50.0


def test_engine_reopen_uses_resolved_denominator():
    result = simulate(
        _baseline(reopened=10, resolved=200),
        {"reopen_rate_target": 5.0},
    )
    assert result["projected"]["reopened_tickets"] == 10
    assert result["projected"]["reopen_rate_percent"] == 5.0
    assert result["deltas"]["reopened_tickets"] == 0


def test_engine_reopen_target_is_percent_of_resolved():
    result = simulate(
        _baseline(reopened=20, resolved=100),
        {"reopen_rate_target": 5.0},
    )
    assert result["projected"]["reopened_tickets"] == 5
    assert result["projected"]["reopen_rate_percent"] == 5.0
    assert result["deltas"]["reopened_tickets"] == -15


def test_engine_reopen_zero_target_projects_zero():
    result = simulate(
        _baseline(reopened=20, resolved=100),
        {"reopen_rate_target": 0.0},
    )
    assert result["projected"]["reopened_tickets"] == 0
    assert result["projected"]["reopen_rate_percent"] == 0.0
    assert result["deltas"]["reopened_tickets"] == -20


def test_engine_reopen_full_target_projects_resolved_count():
    result = simulate(
        _baseline(reopened=20, resolved=100),
        {"reopen_rate_target": 100.0},
    )
    assert result["projected"]["reopened_tickets"] == 100
    assert result["projected"]["reopen_rate_percent"] == 100.0
    assert result["deltas"]["reopened_tickets"] == 80


def test_engine_reopen_zero_resolved_warns_zero_projection():
    result = simulate(
        _baseline(reopened=0, resolved=0),
        {"reopen_rate_target": 5.0},
    )
    assert result["projected"]["reopened_tickets"] == 0
    assert result["projected"]["reopen_rate_percent"] == 5.0
    assert result["deltas"]["reopened_tickets"] == 0
    assert any("zero resolved" in w for w in result["warnings"])


def test_engine_sla_reduction_and_clamp():
    result = simulate(
        _baseline(breaches=120),
        {"sla_breach_reduction_percent": 25.0},
    )
    assert result["projected"]["total_sla_breaches"] == 90
    assert result["projected"]["sla_breach_reduction_percent"] == 25.0

    full = simulate(
        _baseline(breaches=120),
        {"sla_breach_reduction_percent": 100.0},
    )
    assert full["projected"]["total_sla_breaches"] == 0


def test_engine_combined_dimensions_warn_independent_partition():
    result = simulate(
        _baseline(agent_runs=100, autonomous=40, human_approval=30),
        {
            "autonomous_execution_rate_target": 60.0,
            "human_approval_rate_target": 50.0,
        },
    )
    assert result["projected"]["autonomous_executions"] == 60
    assert result["projected"]["human_approval_required"] == 50
    assert any("independent rates, not a mutual partition" in w for w in result["warnings"])


def test_engine_zero_run_warnings():
    result = simulate(
        _baseline(agent_runs=0, autonomous=0, resolved=0),
        {"autonomous_execution_rate_target": 60.0},
    )
    assert result["projected"]["autonomous_executions"] == 0
    assert any("zero observed agent runs" in w for w in result["warnings"])


def test_engine_rejects_invalid_target_range():
    with pytest.raises(ValueError):
        simulate(
            _baseline(),
            {"autonomous_execution_rate_target": 150.0},
        )


# ---------------------------------------------------------------------------
# Value / ROI projection (reuses Phase 1L semantics)
# ---------------------------------------------------------------------------


def test_value_measured_path_scales_and_recomputes_roi():
    result = simulate(
        _baseline(
            agent_runs=100,
            autonomous=40,
            value=_value_baseline(minutes=320.0, labor=133.33, cost=40.0),
        ),
        {"autonomous_execution_rate_target": 100.0},
    )
    v = result["projected"]["value"]
    assert result["measurement_status"] == MEASUREMENT_STATUS_MEASURED
    assert v["measurement_status"] == MEASUREMENT_STATUS_MEASURED
    # scaling = 100/40 = 2.5 -> minutes 800, labor 333.33, net = labor - cost
    assert v["estimated_minutes_saved"] == 800.0
    assert v["estimated_labor_savings_usd"] == 333.33
    assert v["estimated_net_savings_usd"] == round(333.33 - 40.0, 6)
    assert v["roi_percent"] is not None
    assert result["deltas"]["estimated_minutes_saved"] == 480.0
    assert any("autonomous-execution ratio" in w for w in result["warnings"])


def test_value_insufficient_sample_no_roi():
    result = simulate(
        _baseline(
            autonomous=40,
            value=_value_baseline(sample_size_sufficient=False, roi_percent=None),
        ),
        {"autonomous_execution_rate_target": 60.0},
    )
    assert result["measurement_status"] == MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    assert result["projected"]["value"]["roi_percent"] is None
    assert result["projected"]["value"]["measurement_status"] == (
        MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    )
    assert any("minimum autonomous execution sample" in w for w in result["warnings"])


def test_value_pricing_unavailable_no_roi():
    result = simulate(
        _baseline(
            autonomous=40,
            value=_value_baseline(
                pricing_configured=False,
                sample_size_sufficient=True,
                status="pricing_not_configured",
                roi_percent=None,
            ),
        ),
        {"autonomous_execution_rate_target": 60.0},
    )
    assert result["measurement_status"] == MEASUREMENT_STATUS_PRICING_UNAVAILABLE
    assert result["projected"]["value"]["roi_percent"] is None
    assert any("pricing is not configured" in w for w in result["warnings"])


def test_value_zero_instrumented_autonomous():
    result = simulate(
        _baseline(
            autonomous=0,
            value=_value_baseline(
                minutes=0.0, labor=0.0, cost=0.0, roi_percent=None, status="insufficient_sample"
            ),
        ),
        {"autonomous_execution_rate_target": 60.0},
    )
    assert result["measurement_status"] == MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    assert result["projected"]["value"]["roi_percent"] is None
    assert any("zero instrumented autonomous executions" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# API lifecycle, tenancy, RBAC
# ---------------------------------------------------------------------------


async def _create(client, headers, payload=None) -> dict:
    r = await client.post(
        "/service-operations/simulations",
        headers=headers,
        json=payload if payload is not None else _scenario_payload(),
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_create_list_get(client, seeded):
    org_a = seeded["org_a"]
    ha = _auth_headers(USER_SIMA, org_a.id)
    created = await _create(client, ha, _scenario_payload(name="Raise autonomy"))
    assert created["status"] == "draft"
    assert created["organization_id"] == org_a.id
    assert created["window_days"] == 30
    assert created["assumptions"]["autonomous_execution_rate_target"] == 60.0
    assert created["evaluated_at"] is None
    assert created["observed_baseline"] is None

    listed = (await client.get("/service-operations/simulations", headers=ha)).json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    got = (
        await client.get(
            f"/service-operations/simulations/{created['id']}", headers=ha
        )
    ).json()
    assert got["name"] == "Raise autonomy"


@pytest.mark.asyncio
async def test_tenant_isolation(client, seeded):
    org_a = seeded["org_a"]
    org_b = seeded["org_b"]
    ha = _auth_headers(USER_SIMA, org_a.id)
    created = await _create(client, ha)

    # Tenant B can never see or touch tenant A's scenario.
    r = await client.get(
        f"/service-operations/simulations/{created['id']}",
        headers=_auth_headers(USER_SIMB, org_b.id),
    )
    assert r.status_code == 404
    r = await client.post(
        f"/service-operations/simulations/{created['id']}/evaluate",
        headers=_auth_headers(USER_SIMB, org_b.id),
    )
    assert r.status_code == 404
    r = await client.post(
        f"/service-operations/simulations/{created['id']}/archive",
        headers=_auth_headers(USER_SIMB, org_b.id),
    )
    assert r.status_code == 404

    # List is tenant-scoped: B sees zero scenarios.
    listed = (
        await client.get(
            "/service-operations/simulations",
            headers=_auth_headers(USER_SIMB, org_b.id),
        )
    ).json()
    assert listed == []

    # Forged tenant selector on a membership the principal does not hold.
    r = await client.get(
        "/service-operations/simulations",
        headers=_auth_headers(USER_SIMA, org_b.id),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_rbac_gates(client, seeded):
    org_a = seeded["org_a"]

    # Unauthenticated.
    r = await client.get("/service-operations/simulations")
    assert r.status_code == 401
    r = await client.post(
        "/service-operations/simulations",
        json=_scenario_payload(),
    )
    assert r.status_code == 401

    # Viewer and agent are denied read and write.
    for who in (USER_VIEWER, USER_AGENT):
        headers = _auth_headers(who, org_a.id)
        r = await client.get("/service-operations/simulations", headers=headers)
        assert r.status_code == 403, who
        r = await client.post(
            "/service-operations/simulations",
            headers=headers,
            json=_scenario_payload(),
        )
        assert r.status_code == 403, who

    # Supervisor (analyst) can create.
    created = await _create(client, _auth_headers(USER_SUPER, org_a.id))
    assert created["status"] == "draft"

    # Missing membership entirely.
    r = await client.get(
        "/service-operations/simulations",
        headers=_auth_headers(USER_NOBODY),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_evaluate_persists_baseline_and_result(client, seeded, db):
    org_a = seeded["org_a"]
    ha = _auth_headers(USER_SIMA, org_a.id)
    created = await _create(
        client,
        ha,
        _scenario_payload(
            name="Autonomy 80%",
            autonomous=80.0,
            days=30,
        ),
    )

    r = await client.post(
        f"/service-operations/simulations/{created['id']}/evaluate",
        headers=ha,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"]["status"] == "evaluated"
    assert body["scenario"]["formula_version"] == SERVICE_TRANSFORMATION_SIMULATION_VERSION
    assert body["scenario"]["evaluated_at"] is not None
    assert body["assumptions"]["autonomous_execution_rate_target"] == 80.0
    assert body["scenario"]["observed_baseline"] is not None
    assert body["scenario"]["projected_result"] is not None

    # The baseline snapshot was captured from the live Phase 1L summary.
    baseline = body["scenario"]["observed_baseline"]
    assert baseline["organization_id"] == org_a.id
    assert baseline["window_days"] == 30
    assert baseline["agent_runs"] == 3
    assert baseline["autonomous_executions"] == 3
    assert baseline["observed_at"]  # present
    assert body["baseline"]["agent_runs"] == 3

    # Projected counts apply the target to the observed baseline.
    projected = body["projected"]
    assert projected["autonomous_executions"] == 2  # round(3 * 0.8) = 2
    assert projected["autonomous_execution_rate_percent"] == 80.0
    assert body["deltas"]["autonomous_executions"] == -1
    assert body["warnings"]

    # The observed value baseline is insufficient for ROI (2 < 20) and the
    # projection inherits that status instead of fabricating ROI.
    assert body["measurement_status"] == MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    assert projected["value"]["roi_percent"] is None
    assert projected["value"]["measurement_status"] == (
        MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    )

    # A second evaluation refreshes the snapshot on the same row.
    r2 = await client.post(
        f"/service-operations/simulations/{created['id']}/evaluate",
        headers=ha,
    )
    assert r2.status_code == 200
    listed = (
        await client.get("/service-operations/simulations", headers=ha)
    ).json()
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_evaluate_missing_and_archived_rejected(client, seeded):
    org_a = seeded["org_a"]
    ha = _auth_headers(USER_SIMA, org_a.id)
    created = await _create(client, ha)

    r = await client.post(
        "/service-operations/simulations/9999999/evaluate",
        headers=ha,
    )
    assert r.status_code == 404

    await client.post(
        f"/service-operations/simulations/{created['id']}/archive",
        headers=ha,
    )
    r = await client.post(
        f"/service-operations/simulations/{created['id']}/evaluate",
        headers=ha,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_archive_flow(client, seeded):
    org_a = seeded["org_a"]
    ha = _auth_headers(USER_SIMA, org_a.id)
    created = await _create(client, ha)

    archived = (
        await client.post(
            f"/service-operations/simulations/{created['id']}/archive",
            headers=ha,
        )
    ).json()
    assert archived["status"] == "archived"

    # Archiving an already-archived scenario is idempotent.
    again = await client.post(
        f"/service-operations/simulations/{created['id']}/archive",
        headers=ha,
    )
    assert again.status_code == 200
    assert again.json()["status"] == "archived"

    r = await client.post(
        "/service-operations/simulations/9999999/archive",
        headers=ha,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_invalid_requests_rejected(client, seeded):
    org_a = seeded["org_a"]
    ha = _auth_headers(USER_SIMA, org_a.id)

    r = await client.post(
        "/service-operations/simulations",
        headers=ha,
        json={"name": "x", "days": 30, "assumptions": {}},
    )
    assert r.status_code == 422

    r = await client.post(
        "/service-operations/simulations",
        headers=ha,
        json={
            "name": "x",
            "days": 30,
            "assumptions": {"autonomous_execution_rate_target": 101},
        },
    )
    assert r.status_code == 422

    r = await client.post(
        "/service-operations/simulations",
        headers=ha,
        json={
            "name": "x",
            "days": 30,
            "assumptions": {"autonomous_execution_rate_target": 50},
            "organization_id": 1,
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_no_unscoped_and_tenant_scoped_caller(client):
    transformed = [
        REPO_ROOT / "app" / "services" / "service_transformation_simulation_service.py",
        REPO_ROOT / "app" / "repositories" / "service_transformation_scenario_repository.py",
        REPO_ROOT / "app" / "api" / "routes" / "service_transformation_simulation.py",
    ]
    for path in transformed:
        source = path.read_text()
        assert "_unscoped" not in source, f"{path.name} must not call *_unscoped"
        assert "organization_id" in source, (
            f"{path.name} must always scope by organization_id"
        )