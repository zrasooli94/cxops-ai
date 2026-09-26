"""Phase 1O.1 service transformation experiment tests.

Covers the experiment model/migration constraints, the strict create contract,
the pure deterministic outcome-comparison engine (percentage-point semantics,
neutral direction, no baseline mutation), top-level measurement-status
precedence, the persisted non-causal limitations, the API lifecycle
(create/list/get/capture-baseline/start/complete/cancel/archive), tenant
isolation, RBAC gates, and the live Phase 1L-derived baseline/observed
measurement including value/ROI status handling.
"""

# App imports are delayed until after the AUTH_* environment bootstrap +
# warnings filter (repo-wide convention; see the Phase 1N simulation tests).

import json
import os
import uuid
import warnings
from datetime import UTC, datetime, timedelta
from math import nan
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
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
)
from app.models.service_escalation import ServiceEscalation
from app.models.service_queue import ServiceQueue
from app.models.service_transformation_experiment import (
    VALID_EXPERIMENT_MEASUREMENT_STATUSES,
    VALID_EXPERIMENT_SCOPE_TYPES,
    VALID_EXPERIMENT_STATUSES,
    VALID_EXPERIMENT_WINDOWS,
    ServiceTransformationExperiment,
)
from app.models.service_transformation_scenario import ServiceTransformationScenario
from app.models.ticket import Ticket
from app.schemas.service_transformation_experiment import (
    SCOPE_TYPE_ORGANIZATION,
    SCOPE_TYPE_QUEUE,
    ExperimentHypothesis,
    ServiceTransformationExperimentCreate,
)
from app.services.service_transformation_experiment_engine import (
    LIMITATION_NOT_CAUSAL,
    MEASUREMENT_STATUS_INCOMPLETE_WINDOW,
    MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE,
    MEASUREMENT_STATUS_MEASURED,
    MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY,
    MEASUREMENT_STATUS_PRICING_UNAVAILABLE,
    SERVICE_TRANSFORMATION_EXPERIMENT_VERSION,
    compare_outcomes,
)

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-xtf-issuer"
TEST_AUDIENCE = "test-xtf-audience"

USER_SIMA = "user-xtf-a"
USER_SIMB = "user-xtf-b"
USER_SUPER = "user-xtf-super"
USER_VIEWER = "user-xtf-viewer"
USER_AGENT = "user-xtf-agent"

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


def _experiment_payload(
    *,
    name: str = "Raise autonomous coverage",
    scope_type: str = SCOPE_TYPE_ORGANIZATION,
    scope_key: str | None = None,
    target_metrics: dict | None = None,
    baseline_window_days: int = 30,
    measurement_window_days: int = 30,
    description: str | None = None,
    hypothesis: dict | None = None,
    source_scenario_id: int | None = None,
    planned_start_at: str | None = None,
    planned_end_at: str | None = None,
) -> dict:
    payload = {
        "name": name,
        "scope_type": scope_type,
        "scope_key": scope_key,
        "baseline_window_days": baseline_window_days,
        "measurement_window_days": measurement_window_days,
        "hypothesis": hypothesis
        or {
            "summary": "Autonomous coverage uplift pilot",
            "change_description": "Enable policy_auto for low-risk tools",
            "expected_direction": {
                "autonomous_execution_rate": "increase",
            },
        },
        "target_metrics": target_metrics
        or {
            "autonomous_execution_rate": 60.0,
            "knowledge_usage_rate": 50.0,
        },
        "source_scenario_id": source_scenario_id,
    }
    if description is not None:
        payload["description"] = description
    if planned_start_at is not None:
        payload["planned_start_at"] = planned_start_at
    if planned_end_at is not None:
        payload["planned_end_at"] = planned_end_at
    return payload


@pytest_asyncio.fixture
async def seeded(db):
    """Two kept-apart tenants (Org A owns all telemetry; Org B is isolated).

    Org A has three agent runs (all policy_auto with knowledge paths) for one
    still-open ticket with an in-window first response, so the org baseline is
    live: agent_runs=3, autonomous rate 100%, knowledge rate 100%, and a value
    measurement of ``insufficient_sample`` (2 instrumented < min sample 20).
    Org B carries no telemetry and must never observe Org A rows.
    """
    org_ids: list[int] = []

    leaked = await db.execute(
        select(Organization.id).where(Organization.name.like("exp1o-%"))
    )
    leaked_ids = [row[0] for row in leaked.all()]
    for tbl, col in [
        (ServiceTransformationExperiment, "organization_id"),
        (ServiceTransformationScenario, "organization_id"),
        (AIRequestLog, "organization_id"),
        (AgentRun, "organization_id"),
        (ServiceEscalation, "organization_id"),
        (Ticket, "organization_id"),
        (ServiceQueue, "organization_id"),
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
        f"exp1o-a-{uuid.uuid4().hex[:8]}",
        subject=USER_SIMA,
        role=OrganizationRole.OWNER,
    )
    org_b = await make_org(
        f"exp1o-b-{uuid.uuid4().hex[:8]}",
        subject=USER_SIMB,
        role=OrganizationRole.OWNER,
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
            workflow_path=["coordinator", "knowledge_specialist", "action_specialist"],
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


async def _create_experiment(client, headers, payload=None) -> dict:
    resp = await client.post(
        "/service-operations/experiments",
        headers=headers,
        json=payload or _experiment_payload(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _to_completed_org_experiment(client, headers, experiment_id: str | int) -> dict:
    resp = await client.post(
        f"/service-operations/experiments/{experiment_id}/capture-baseline",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/service-operations/experiments/{experiment_id}/start",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/service-operations/experiments/{experiment_id}/complete",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_policy_auto_runs(
    db,
    org_id: int,
    *,
    count: int = 3,
    queue_id: int | None = None,
    instrumented: int | None = None,
    at: datetime | None = None,
) -> int:
    """Create executed policy_auto runs inside the observed window.

    Returns the number of tickets created. ``instrumented`` of the runs also
    get an AIRequestLog so they count toward the autonomous-execution sample;
    the measurement window is the observed window (opens at baseline capture),
    so telemetry added after ``start`` lands inside it.
    """
    if at is None:
        at = datetime.now(UTC)
    if instrumented is None:
        instrumented = count
    for i in range(count):
        ticket = Ticket(
            organization_id=org_id,
            subject=f"obs-{uuid.uuid4().hex[:8]}",
            description="support case",
            status="open",
            priority="normal",
            service_queue_id=queue_id,
            created_at=at - timedelta(minutes=1),
            resolved_at=None,
            first_response_at=at,
            first_response_due_at=at,
        )
        db.add(ticket)
        await db.flush()
        run = AgentRun(
            run_id=uuid.uuid4().hex,
            ticket_id=ticket.id,
            organization_id=org_id,
            action="suggest",
            reason="reason",
            response_draft="draft",
            status="executed",
            sources=[],
            workflow_path=["coordinator", "knowledge_specialist", "action_specialist"],
            tool_plan=[],
            authorization_source="policy_auto",
            reviewer_note=AUTO_NOTE,
            created_at=at,
        )
        db.add(run)
        await db.flush()
        if i < instrumented:
            db.add(
                AIRequestLog(
                    organization_id=org_id,
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
    return count


async def _make_ticket(
    db,
    *,
    organization_id: int,
    created: datetime,
    queue_id: int | None = None,
    status: str = "solved",
    first_response_at: datetime | None = None,
    first_response_due_at: datetime | None = None,
    resolved_at: datetime | None = None,
    resolution_due_at: datetime | None = None,
) -> Ticket:
    ticket = Ticket(
        organization_id=organization_id,
        subject=f"esc-{uuid.uuid4().hex[:8]}",
        description="support case",
        status=status,
        priority="normal",
        service_queue_id=queue_id,
        created_at=created,
        first_response_at=first_response_at,
        first_response_due_at=first_response_due_at,
        resolved_at=resolved_at,
        resolution_due_at=resolution_due_at,
    )
    db.add(ticket)
    await db.flush()
    return ticket


async def _add_escalation(
    db,
    *,
    organization_id: int,
    ticket: Ticket,
    milestone: str,
    stage: str,
    status: str,
    triggered_at: datetime,
) -> ServiceEscalation:
    esc = ServiceEscalation(
        organization_id=organization_id,
        ticket_id=ticket.id,
        milestone=milestone,
        stage=stage,
        status=status,
        event_key=uuid.uuid4().hex,
        triggered_at=triggered_at,
        resolved_at=None,
        resolution_reason=None,
        resolution_sla_cycle=0,
        source="sla_monitor",
        transition_version=1,
    )
    db.add(esc)
    await db.flush()
    return esc


async def _seed_canonical_sla_evidence(
    db,
    *,
    organization_id: int,
    queue_id: int | None = None,
    baseline: tuple[int, int] = (0, 0),
    observed: tuple[int, int] = (0, 0),
) -> tuple[list[Ticket], list[Ticket]]:
    """Seed persisted ServiceEscalation breach evidence in both windows.

    ``baseline`` / ``observed`` are ``(first_response, resolution)`` breach
    counts, each on its own dedicated carrier ticket scoped to ``queue_id``.
    Baseline rows are triggered 20 days back (inside a 30-day baseline
    window); observed rows are triggered moments ago (after the experiment's
    ``actual_started_at``, strictly inside its run window) so they land inside
    the experiment's run window but strictly after ``actual_started_at``. Carrier
    tickets never carry first-response/resolution timestamps, so
    ticket-deadline analytics sees zero breaches for them.
    """
    now = datetime.now(UTC)
    baseline_tickets: list[Ticket] = []
    observed_tickets: list[Ticket] = []
    for _ in range(baseline[0]):
        carrier = await _make_ticket(
            db,
            organization_id=organization_id,
            created=now - timedelta(days=45),
            queue_id=queue_id,
            status="solved",
        )
        await _add_escalation(
            db,
            organization_id=organization_id,
            ticket=carrier,
            milestone="first_response",
            stage="breached",
            status="resolved",
            triggered_at=now - timedelta(days=20),
        )
        baseline_tickets.append(carrier)
    for _ in range(baseline[1]):
        carrier = await _make_ticket(
            db,
            organization_id=organization_id,
            created=now - timedelta(days=45),
            queue_id=queue_id,
            status="solved",
        )
        await _add_escalation(
            db,
            organization_id=organization_id,
            ticket=carrier,
            milestone="resolution",
            stage="breached",
            status="resolved",
            triggered_at=now - timedelta(days=20),
        )
        baseline_tickets.append(carrier)
    for _ in range(observed[0]):
        carrier = await _make_ticket(
            db,
            organization_id=organization_id,
            created=now - timedelta(minutes=2),
            queue_id=queue_id,
            status="solved",
        )
        await _add_escalation(
            db,
            organization_id=organization_id,
            ticket=carrier,
            milestone="first_response",
            stage="breached",
            status="resolved",
            triggered_at=now,
        )
        observed_tickets.append(carrier)
    for _ in range(observed[1]):
        carrier = await _make_ticket(
            db,
            organization_id=organization_id,
            created=now - timedelta(minutes=2),
            queue_id=queue_id,
            status="solved",
        )
        await _add_escalation(
            db,
            organization_id=organization_id,
            ticket=carrier,
            milestone="resolution",
            stage="breached",
            status="resolved",
            triggered_at=now,
        )
        observed_tickets.append(carrier)
    await db.flush()
    return baseline_tickets, observed_tickets


# ---------------------------------------------------------------------------
# Snapshot builders for the pure engine
# ---------------------------------------------------------------------------


def _value_block(
    *,
    status: str = "measured",
    net: float = 126.67,
    roi: float | None = 316.67,
    sample_size_sufficient: bool = True,
) -> dict:
    return {
        "estimated_minutes_saved": 400.0,
        "estimated_hours_saved": 6.67,
        "estimated_labor_savings_usd": 166.67,
        "agent_ai_cost_usd": 40.0,
        "estimated_net_savings_usd": net,
        "pricing_configured": True,
        "measurement_status": status,
        "minimum_autonomous_samples": settings.roi_min_autonomous_samples,
        "sample_size_sufficient": sample_size_sufficient,
        "roi_percent": roi,
    }


def _snapshot(
    *,
    window_days: float = 30.0,
    autonomous_rate: float | None = None,
    human_rate: float | None = None,
    knowledge_rate: float | None = None,
    reopen_rate: float | None = None,
    agent_runs: int | None = None,
    tickets_resolved: int | None = None,
    first_responses: int | None = None,
    reopen_events: int | None = None,
    total_sla_breaches: int | None = None,
    fr_minutes: float | None = None,
    res_minutes: float | None = None,
    value: dict | None = None,
) -> dict:
    return {
        "window_days": window_days,
        "current_window_start": "2026-01-01T00:00:00+00:00",
        "current_window_end": "2026-01-31T00:00:00+00:00",
        "tickets_resolved": tickets_resolved,
        "first_responses": first_responses,
        "reopen_events": reopen_events,
        "total_sla_breaches": total_sla_breaches,
        "agent_runs": agent_runs,
        "average_first_response_minutes": fr_minutes,
        "average_resolution_time_minutes": res_minutes,
        "rates": {
            "autonomous_execution_rate": autonomous_rate,
            "human_approval_rate": human_rate,
            "knowledge_usage_rate": knowledge_rate,
            "reopen_rate": reopen_rate,
        },
        "value": value,
    }


def _baseline_30d() -> dict:
    return _snapshot(
        window_days=30,
        autonomous_rate=40.0,
        knowledge_rate=20.0,
        reopen_rate=5.0,
        agent_runs=100,
        tickets_resolved=200,
        first_responses=190,
        reopen_events=10,
        total_sla_breaches=120,
        fr_minutes=14.0,
        res_minutes=170.0,
        value=_value_block(),
    )


def _observed_30d() -> dict:
    return _snapshot(
        window_days=30,
        autonomous_rate=65.0,
        knowledge_rate=30.0,
        reopen_rate=4.0,
        agent_runs=110,
        tickets_resolved=210,
        first_responses=200,
        reopen_events=9,
        total_sla_breaches=90,
        fr_minutes=12.0,
        res_minutes=160.0,
        value=_value_block(net=140.0, roi=350.0),
    )


def _projected_dict() -> dict:
    return {
        "autonomous_executions": 66,
        "autonomous_execution_rate_percent": 60.0,
        "human_approval_required": 30,
        "human_approval_rate_percent": 30.0,
        "knowledge_specialist_runs": 50,
        "knowledge_usage_rate_percent": 50.0,
        "reopened_tickets": 10,
        "reopen_rate_percent": 5.0,
        "total_sla_breaches": 100,
        "sla_breach_reduction_percent": 20.0,
        "value": _value_block(net=130.0, roi=320.0),
    }


def _targets() -> dict:
    return {
        "autonomous_execution_rate": 60.0,
        "knowledge_usage_rate": 50.0,
        "reopen_rate": 5.0,
        "total_sla_breaches": 90.0,
        "average_resolution_time_minutes": 150.0,
        "roi_percent": 300.0,
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


def test_experiment_model_org_not_null_and_constraints():
    column = ServiceTransformationExperiment.__table__.c.organization_id
    assert column.nullable is False
    ck_names = {
        c.name
        for c in ServiceTransformationExperiment.__table__.constraints
        if c.name and c.name.startswith("ck_")
    }
    for name in (
        "ck_service_transformation_experiments_status",
        "ck_service_transformation_experiments_scope_type",
        "ck_service_transformation_experiments_baseline_window_days",
        "ck_service_transformation_experiments_measurement_window_days",
        "ck_service_transformation_experiments_measurement_status",
        "ck_service_transformation_experiments_scope_scope_key",
    ):
        assert name in ck_names, name
    index_cols = {
        i.name: {c.name for c in i.columns}
        for i in ServiceTransformationExperiment.__table__.indexes
    }
    assert index_cols["ix_service_transformation_experiments_organization_id"] == {
        "organization_id"
    }
    assert (
        index_cols["ix_service_transformation_experiments_org_id_created_at"]
        == {"organization_id", "created_at"}
    )
    assert VALID_EXPERIMENT_WINDOWS == (7, 30, 90)
    assert VALID_EXPERIMENT_STATUSES == (
        "draft",
        "ready",
        "running",
        "completed",
        "cancelled",
        "archived",
    )
    assert VALID_EXPERIMENT_SCOPE_TYPES == ("organization", "queue")
    assert len(VALID_EXPERIMENT_MEASUREMENT_STATUSES) == 6


# ---------------------------------------------------------------------------
# Create contract
# ---------------------------------------------------------------------------


def test_create_rejects_org_id_in_body():
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **_experiment_payload(),
            organization_id=42,  # type: ignore[call-arg]
        )


def test_create_rejects_blank_name():
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **_experiment_payload(name="   ")
        )


def test_create_rejects_empty_targets_and_unknown_metric():
    payload = _experiment_payload()
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{**payload, "target_metrics": {}}
        )
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{**payload, "target_metrics": {"made_up_metric": 50.0}}
        )


def test_create_rejects_non_finite_and_out_of_bounds_targets():
    payload = _experiment_payload()
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{**payload, "target_metrics": {"autonomous_execution_rate": nan}}
        )
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{**payload, "target_metrics": {"autonomous_execution_rate": 100.1}}
        )
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{**payload, "target_metrics": {"total_sla_breaches": -1}}
        )
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{**payload, "target_metrics": {"roi_percent": nan}}
        )


def test_create_accepts_negative_roi_and_rejects_invalid_windows():
    payload = _experiment_payload(
        target_metrics={"roi_percent": -50.0},
    )
    assert ServiceTransformationExperimentCreate(**payload).target_metrics == {
        "roi_percent": -50.0
    }
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{**_experiment_payload(), "baseline_window_days": 15}
        )


def test_create_scope_rules_and_value_exclusion_for_queue():
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **_experiment_payload(scope_type=SCOPE_TYPE_QUEUE)
        )
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **_experiment_payload(scope_key="ops-1")
        )
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **_experiment_payload(
                scope_type=SCOPE_TYPE_QUEUE,
                scope_key="ops-1",
                target_metrics={"roi_percent": 300.0},
            )
        )
    valid = ServiceTransformationExperimentCreate(
        **_experiment_payload(
            scope_type=SCOPE_TYPE_QUEUE,
            scope_key="ops-1",
            target_metrics={"autonomous_execution_rate": 60.0},
        )
    )
    assert valid.scope_type == SCOPE_TYPE_QUEUE


def test_create_hypothesis_and_schedule_validation():
    payload = _experiment_payload()
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{
                **payload,
                "hypothesis": {
                    "summary": "x",
                    "expected_direction": {"made_up_metric": "increase"},
                },
            }
        )
    with pytest.raises(ValueError):
        ServiceTransformationExperimentCreate(
            **{
                **payload,
                "planned_start_at": "2026-02-01T00:00:00Z",
                "planned_end_at": "2026-01-01T00:00:00Z",
            }
        )
    assert ExperimentHypothesis(summary="  pilot  ").summary == "  pilot  "


# ---------------------------------------------------------------------------
# Pure outcome-comparison engine
# ---------------------------------------------------------------------------


def test_engine_deterministic_and_does_not_mutate_inputs():
    baseline = _baseline_30d()
    observed = _observed_30d()
    targets = _targets()
    baseline_before = dict(baseline)
    observed_before = dict(observed)
    a = compare_outcomes(
        baseline=baseline,
        targets=targets,
        observed=observed,
        planned_measurement_days=30,
    )
    b = compare_outcomes(
        baseline=baseline,
        targets=targets,
        observed=observed,
        planned_measurement_days=30,
    )
    assert a == b
    assert baseline == baseline_before
    assert observed == observed_before


def test_engine_percentage_point_and_count_semantics():
    result = compare_outcomes(
        baseline=_baseline_30d(),
        targets=_targets(),
        observed=_observed_30d(),
        planned_measurement_days=30,
    )
    metrics = {m["metric"]: m for m in result["metrics"]}
    rate = metrics["autonomous_execution_rate"]
    assert rate["baseline_value"] == 40.0
    assert rate["target_value"] == 60.0
    assert rate["observed_value"] == 65.0
    assert rate["change_from_baseline"] == 25.0
    assert rate["variance_from_target"] == 5.0
    assert rate["unit"] == "percentage_points"
    assert rate["direction_vs_baseline"] == "increased"
    assert rate["direction_vs_target"] == "increased"
    assert rate["measurement_status"] == MEASUREMENT_STATUS_MEASURED

    breaches = metrics["total_sla_breaches"]
    assert breaches["baseline_value"] == 120
    assert breaches["observed_value"] == 90
    assert breaches["change_from_baseline"] == -30
    assert breaches["variance_from_target"] == 0.0
    assert breaches["direction_vs_baseline"] == "decreased"
    assert breaches["direction_vs_target"] == "unchanged"
    assert breaches["unit"] == "count"


def test_engine_neutral_direction_never_inverts_lower_is_better():
    result = compare_outcomes(
        baseline=_baseline_30d(),
        targets=_targets(),
        observed=_observed_30d(),
        planned_measurement_days=30,
    )
    metrics = {m["metric"]: m for m in result["metrics"]}
    reopen = metrics["reopen_rate"]
    assert reopen["change_from_baseline"] == -1.0
    assert reopen["direction_vs_baseline"] == "decreased"
    assert reopen["direction_vs_target"] == "decreased"
    assert reopen["variance_from_target"] == -1.0
    for label in ("direction_vs_baseline", "direction_vs_target"):
        assert metrics["reopen_rate"][label] in (
            "increased",
            "decreased",
            "unchanged",
            "not_applicable",
        )
    for row in result["metrics"]:
        assert not any(
            word in str(row)
            for word in ("improved", "worse", "good", "bad", "success", "failed")
        ), row


def test_engine_projection_comparison_and_different_window_warning():
    result = compare_outcomes(
        baseline=_baseline_30d(),
        targets=_targets(),
        observed=_observed_30d(),
        projected=_projected_dict(),
        source_window_days=30,
        source_evaluated=True,
        planned_measurement_days=30,
    )
    metrics = {m["metric"]: m for m in result["metrics"]}
    assert metrics["autonomous_execution_rate"]["projected_value"] == 60.0
    assert metrics["autonomous_execution_rate"]["variance_from_projection"] == 5.0
    assert metrics["roi_percent"]["projected_value"] == 320.0
    assert metrics["roi_percent"]["variance_from_projection"] == 30.0
    assert metrics["total_sla_breaches"]["projected_value"] == 100
    assert metrics["total_sla_breaches"]["variance_from_projection"] == -10
    assert metrics["average_resolution_time_minutes"]["projected_value"] is None
    assert (
        metrics["average_resolution_time_minutes"]["variance_from_projection"] is None
    )
    assert metrics["average_resolution_time_minutes"]["direction_vs_projection"] == (
        "not_applicable"
    )

    mismatch = compare_outcomes(
        baseline=_baseline_30d(),
        targets=_targets(),
        observed=_observed_30d(),
        projected=_projected_dict(),
        source_window_days=90,
        source_evaluated=True,
        planned_measurement_days=30,
    )
    assert any("90-day window" in w for w in mismatch["warnings"])
    assert any("not directly comparable" in l for l in mismatch["limitations"])


def test_engine_source_not_evaluated_only_when_linked():
    linked = compare_outcomes(
        baseline=_baseline_30d(),
        targets=_targets(),
        observed=_observed_30d(),
        projected=None,
        source_window_days=30,
        source_evaluated=False,
        planned_measurement_days=30,
    )
    assert any(
        "has not been evaluated" in l for l in linked["limitations"]
    )
    assert any(
        "has not been evaluated" in w for w in linked["warnings"]
    )
    for row in linked["metrics"]:
        assert row["projected_value"] is None
        assert row["variance_from_projection"] is None

    unrelated = compare_outcomes(
        baseline=_baseline_30d(),
        targets=_targets(),
        observed=_observed_30d(),
        planned_measurement_days=30,
    )
    assert not any(
        "has not been evaluated" in l for l in unrelated["limitations"]
    )
    assert not any(
        "has not been evaluated" in w for w in unrelated["warnings"]
    )


def test_engine_top_level_measurement_statuses():
    targets = _targets()
    measured = compare_outcomes(
        baseline=_baseline_30d(),
        targets=targets,
        observed=_observed_30d(),
        planned_measurement_days=30,
    )
    assert measured["measurement_status"] == MEASUREMENT_STATUS_MEASURED

    priced_out = compare_outcomes(
        baseline=_baseline_30d(),
        targets=targets,
        observed=_snapshot(
            window_days=30,
            autonomous_rate=65.0,
            reopen_rate=4.0,
            agent_runs=110,
            tickets_resolved=210,
            total_sla_breaches=90,
            value=_value_block(status="pricing_unavailable", roi=None),
        ),
        planned_measurement_days=30,
    )
    assert priced_out["measurement_status"] == MEASUREMENT_STATUS_PRICING_UNAVAILABLE

    short_sample = compare_outcomes(
        baseline=_baseline_30d(),
        targets=targets,
        observed=_snapshot(
            window_days=30,
            autonomous_rate=65.0,
            reopen_rate=4.0,
            agent_runs=110,
            tickets_resolved=210,
            total_sla_breaches=90,
            value=_value_block(
                status="insufficient_sample", roi=None, sample_size_sufficient=False
            ),
        ),
        planned_measurement_days=30,
    )
    assert short_sample["measurement_status"] == MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE

    partial = compare_outcomes(
        baseline=_baseline_30d(),
        targets=targets,
        observed=_snapshot(
            window_days=2.0,
            autonomous_rate=65.0,
            reopen_rate=4.0,
            agent_runs=110,
            tickets_resolved=210,
            total_sla_breaches=90,
            value=_value_block(),
        ),
        planned_measurement_days=30,
    )
    assert partial["measurement_status"] == MEASUREMENT_STATUS_INCOMPLETE_WINDOW
    assert any("shorter than the planned" in l for l in partial["limitations"])


def test_engine_no_observed_activity():
    result = compare_outcomes(
        baseline=_baseline_30d(),
        targets={"autonomous_execution_rate": 60.0, "reopen_rate": 5.0},
        observed=_snapshot(
            window_days=30,
            autonomous_rate=None,
            reopen_rate=None,
            agent_runs=None,
            tickets_resolved=None,
            first_responses=None,
            reopen_events=None,
            total_sla_breaches=None,
        ),
        planned_measurement_days=30,
    )
    assert result["measurement_status"] == MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY
    for row in result["metrics"]:
        assert row["observed_value"] is None
        assert row["change_from_baseline"] is None
        assert row["measurement_status"] == MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY
        assert row["warning"] is not None


def test_engine_limitations_are_present_no_causal_claims():
    result = compare_outcomes(
        baseline=_baseline_30d(),
        targets=_targets(),
        observed=_observed_30d(),
        projected=_projected_dict(),
        source_window_days=90,
        source_evaluated=True,
        planned_measurement_days=30,
    )
    limitations = result["limitations"]
    assert limitations[0] == LIMITATION_NOT_CAUSAL
    assert len(limitations) >= 5
    assert all(isinstance(text, str) and text for text in limitations)

    serialized = json.dumps(result)
    assert LIMITATION_NOT_CAUSAL in serialized
    assert result["comparison_version"] == SERVICE_TRANSFORMATION_EXPERIMENT_VERSION
    assert "window" in result
    # The always-first disclaimer legitimately negates causation ("does not
    # establish that the intervention caused the improvement"), so the bans
    # target affirmative causal/final language only.
    for banned in (
        "proves",
        "demonstrate",
        "guarantee",
        "causally linked",
        "causes the",
        "because of the experiment",
    ):
        assert banned not in serialized, banned


def test_engine_rejects_invalid_targets_and_window():
    with pytest.raises(ValueError):
        compare_outcomes(
            baseline=_baseline_30d(),
            targets={"made_up_metric": 1.0},
            observed=_observed_30d(),
            planned_measurement_days=30,
        )
    with pytest.raises(ValueError):
        compare_outcomes(
            baseline=_baseline_30d(),
            targets={"autonomous_execution_rate": 200.0},
            observed=_observed_30d(),
            planned_measurement_days=30,
        )
    with pytest.raises(ValueError):
        compare_outcomes(
            baseline=_baseline_30d(),
            targets={"total_sla_breaches": -5.0},
            observed=_observed_30d(),
            planned_measurement_days=0,
        )


# ---------------------------------------------------------------------------
# API lifecycle
# ---------------------------------------------------------------------------


def _org_a_headers(tenant_id: int) -> dict:
    return _auth_headers(USER_SIMA, tenant_id)


@pytest.mark.asyncio
async def test_create_list_get_and_created_by_subject(client, seeded, db):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            name="Explicit experiment",
            source_scenario_id=None,
        ),
    )
    assert created["status"] == "draft"
    assert created["scope_type"] == SCOPE_TYPE_ORGANIZATION
    assert created["scope_key"] is None
    assert created["created_by_subject"] == USER_SIMA
    assert created["measurement_status"] == "not_measured"
    assert created["baseline_snapshot"] is None
    assert created["observed_outcome"] is None
    assert created["outcome_comparison"] is None

    listed = await client.get(
        "/service-operations/experiments", headers=headers
    )
    assert listed.status_code == 200
    assert any(item["id"] == created["id"] for item in listed.json())

    detail = await client.get(
        f"/service-operations/experiments/{created['id']}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["name"] == "Explicit experiment"


@pytest.mark.asyncio
async def test_capture_baseline_from_live_telemetry(client, seeded, db):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(client, headers)
    resp = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["baseline_captured_at"] is not None
    snap = body["baseline_snapshot"]
    assert snap["scope_type"] == SCOPE_TYPE_ORGANIZATION
    assert snap["window_days"] == 30
    assert snap["agent_runs"] == 3
    assert snap["autonomous_executions"] == 3
    assert snap["rates"]["autonomous_execution_rate"] == 100.0
    assert snap["rates"]["knowledge_usage_rate"] == 100.0
    assert snap["tickets_resolved"] == 0
    assert snap["value"]["measurement_status"] == "insufficient_sample"
    assert snap["value"]["roi_percent"] is None


@pytest.mark.asyncio
async def test_capture_baseline_twice_conflicts(client, seeded):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(client, headers)
    first = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert first.status_code == 200
    second = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_start_transitions_and_rejects_before_baseline(client, seeded):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(client, headers)
    before = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert before.status_code == 409
    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200
    started = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert started.json()["actual_started_at"] is not None
    again = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_complete_measures_observed_outcome_and_guards(client, seeded, db):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            target_metrics={"autonomous_execution_rate": 60.0, "roi_percent": 300.0}
        ),
    )
    too_early = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert too_early.status_code == 409

    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    started = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert started.status_code == 200, started.text
    await _add_policy_auto_runs(db, seeded["org_a"].id, count=3, instrumented=2)

    resp = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    done = resp.json()
    assert done["status"] == "completed"
    assert done["measured_at"] is not None
    assert done["actual_ended_at"] is not None
    assert done["comparison_version"] == SERVICE_TRANSFORMATION_EXPERIMENT_VERSION
    assert done["observed_outcome"]["agent_runs"] == 3
    assert done["observed_outcome"]["autonomous_executions"] == 3
    assert done["measurement_status"] == MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    comparison = done["outcome_comparison"]
    assert comparison["limitations"][0] == LIMITATION_NOT_CAUSAL
    roi_row = next(
        m for m in comparison["metrics"] if m["metric"] == "roi_percent"
    )
    assert roi_row["observed_value"] is None
    assert roi_row["measurement_status"] == MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    assert roi_row["warning"] is not None
    second = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert second.status_code == 409

    serialized = json.dumps(done["outcome_comparison"])
    assert LIMITATION_NOT_CAUSAL in serialized
    for banned in (
        "proves",
        "demonstrate",
        "guarantee",
        "causally linked",
        "causes the",
        "because of the experiment",
    ):
        assert banned not in serialized, banned


@pytest.mark.asyncio
async def test_cancel_and_archive_lifecycle(client, seeded):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(client, headers)
    cancelled = await client.post(
        f"/service-operations/experiments/{created['id']}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    again = await client.post(
        f"/service-operations/experiments/{created['id']}/cancel",
        headers=headers,
    )
    assert again.status_code == 409

    archived = await client.post(
        f"/service-operations/experiments/{created['id']}/archive",
        headers=headers,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    idempotent = await client.post(
        f"/service-operations/experiments/{created['id']}/archive",
        headers=headers,
    )
    assert idempotent.status_code == 200
    assert idempotent.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_running_cannot_be_archived(client, seeded):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(client, headers)
    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200
    start = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert start.status_code == 200
    archive = await client.post(
        f"/service-operations/experiments/{created['id']}/archive",
        headers=headers,
    )
    assert archive.status_code == 409
    cancel = await client.post(
        f"/service-operations/experiments/{created['id']}/cancel",
        headers=headers,
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Queue scope (live measurement with queue filter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_scope_lifecycle_measures_with_queue_filter(client, seeded, db):
    headers = _org_a_headers(seeded["org_a"].id)
    org_id = seeded["org_a"].id
    now = datetime.now(UTC)

    queue = ServiceQueue(
        organization_id=org_id,
        key="xtf-queue-econ",
        name="Queue Econ",
        active=True,
        is_default=False,
    )
    db.add(queue)
    await db.flush()

    for i in range(2):
        ticket = Ticket(
            organization_id=org_id,
            subject=f"q-{uuid.uuid4().hex[:8]}",
            description="support case",
            status="resolved",
            priority="normal",
            service_queue_id=queue.id,
            created_at=now - timedelta(days=1),
            resolved_at=now - timedelta(hours=1),
            resolution_due_at=now,
        )
        db.add(ticket)
        await db.flush()
        run = AgentRun(
            run_id=uuid.uuid4().hex,
            ticket_id=ticket.id,
            organization_id=org_id,
            action="suggest",
            reason="reason",
            response_draft="draft",
            status="executed",
            sources=[],
            workflow_path=["coordinator", "action"],
            tool_plan=[],
            authorization_source="policy_auto",
            reviewer_note=AUTO_NOTE,
            created_at=now - timedelta(days=1),
        )
        db.add(run)
    await db.commit()

    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            scope_type=SCOPE_TYPE_QUEUE,
            scope_key="xtf-queue-econ",
            target_metrics={
                "autonomous_execution_rate": 100.0,
                "total_sla_breaches": 0.0,
            },
        ),
    )
    assert created["scope_type"] == SCOPE_TYPE_QUEUE
    assert created["scope_key"] == "xtf-queue-econ"

    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    snap = cap.json()["baseline_snapshot"]
    assert snap["scope_type"] == SCOPE_TYPE_QUEUE
    assert snap["scope_key"] == "xtf-queue-econ"
    assert snap["value"] is None
    assert snap["agent_runs"] == 2
    assert snap["autonomous_executions"] == 2
    assert snap["total_sla_breaches"] == 0
    assert snap["tickets_resolved"] == 2
    assert snap["rates"]["autonomous_execution_rate"] == 100.0

    start = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert start.status_code == 200
    await _add_policy_auto_runs(
        db, org_id, count=2, queue_id=queue.id, instrumented=0
    )
    done = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "completed"
    assert body["observed_outcome"]["value"] is None
    assert body["observed_outcome"]["agent_runs"] == 2
    assert body["measurement_status"] == MEASUREMENT_STATUS_INCOMPLETE_WINDOW
    assert body["outcome_comparison"]["limitations"][0] == LIMITATION_NOT_CAUSAL


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_isolation_all_endpoints(client, seeded):
    headers_a = _org_a_headers(seeded["org_a"].id)
    headers_b = _auth_headers(USER_SIMB, seeded["org_b"].id)
    created = await _create_experiment(client, headers_a)

    listed_b = await client.get(
        "/service-operations/experiments", headers=headers_b
    )
    assert listed_b.status_code == 200
    assert listed_b.json() == []

    detail = await client.get(
        f"/service-operations/experiments/{created['id']}", headers=headers_b
    )
    assert detail.status_code == 404

    for path in (
        "capture-baseline",
        "start",
        "complete",
        "cancel",
        "archive",
    ):
        resp = await client.post(
            f"/service-operations/experiments/{created['id']}/{path}",
            headers=headers_b,
        )
        assert resp.status_code == 404, path


@pytest.mark.asyncio
async def test_cross_tenant_queue_and_scenario_rejected(client, seeded, db):
    headers_a = _org_a_headers(seeded["org_a"].id)
    org_a = seeded["org_a"].id
    org_b = seeded["org_b"].id

    queue_b = ServiceQueue(
        organization_id=org_b,
        key="queue-b-only",
        name="Queue B",
        active=True,
        is_default=False,
    )
    scenario_b = ServiceTransformationScenario(
        organization_id=org_b,
        name="scenario-b-only",
        window_days=30,
        assumptions={"reopen_rate_target": 2.0},
    )
    db.add(queue_b)
    db.add(scenario_b)
    await db.commit()

    resp = await client.post(
        "/service-operations/experiments",
        headers=headers_a,
        json=_experiment_payload(
            scope_type=SCOPE_TYPE_QUEUE,
            scope_key="queue-b-only",
            target_metrics={"autonomous_execution_rate": 50.0},
        ),
    )
    assert resp.status_code == 400
    assert "does not name a queue" in resp.json()["detail"]

    resp = await client.post(
        "/service-operations/experiments",
        headers=headers_a,
        json=_experiment_payload(source_scenario_id=scenario_b.id),
    )
    assert resp.status_code == 400
    assert "does not name a scenario" in resp.json()["detail"]

    _ = org_a
    _ = await client.get("/experiments", headers=headers_a)


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_gates(client, seeded):
    tenant_id = seeded["org_a"].id
    owner = _auth_headers(USER_SIMA, tenant_id)
    supervisor = _auth_headers(USER_SUPER, tenant_id)
    viewer = _auth_headers(USER_VIEWER, tenant_id)
    agent = _auth_headers(USER_AGENT, tenant_id)

    created = await _create_experiment(client, owner)

    super_read = await client.get(
        f"/service-operations/experiments/{created['id']}",
        headers=supervisor,
    )
    assert super_read.status_code == 200

    super_cap = await client.post(
        f"/service-operations/experiments/{created['id']}/cancel",
        headers=supervisor,
    )
    assert super_cap.status_code == 200

    for roles in (viewer, agent):
        for method, path in (
            ("get", "/service-operations/experiments"),
            ("post", "/service-operations/experiments"),
            ("get", f"/service-operations/experiments/{created['id']}"),
            ("post", f"/service-operations/experiments/{created['id']}/cancel"),
        ):
            resp = await client.request(method, path, headers=roles)
            assert resp.status_code == 403, (roles, method, path)

    no_auth = await client.get("/service-operations/experiments")
    assert no_auth.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Source scenario linkage and projection comparison via API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_scenario_projection_persisted_and_compared(client, seeded, db):
    headers = _org_a_headers(seeded["org_a"].id)
    org_id = seeded["org_a"].id

    scenario = ServiceTransformationScenario(
        organization_id=org_id,
        name="scenario-for-experiment",
        window_days=30,
        assumptions={"autonomous_execution_rate_target": 60.0},
        status="evaluated",
        formula_version="1",
        projected_result={
            "autonomous_executions": 60,
            "autonomous_execution_rate_percent": 60.0,
            "human_approval_required": 0,
            "human_approval_rate_percent": None,
            "knowledge_specialist_runs": 0,
            "knowledge_usage_rate_percent": None,
            "reopened_tickets": 0,
            "reopen_rate_percent": None,
            "total_sla_breaches": 0,
            "sla_breach_reduction_percent": None,
            "value": {
                "estimated_minutes_saved": 480.0,
                "estimated_hours_saved": 8.0,
                "estimated_labor_savings_usd": 200.0,
                "agent_ai_cost_usd": 40.0,
                "estimated_net_savings_usd": 160.0,
                "roi_percent": 300.0,
                "measurement_status": "measured",
            },
        },
    )
    db.add(scenario)
    await db.commit()

    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            target_metrics={"autonomous_execution_rate": 60.0},
            source_scenario_id=scenario.id,
        ),
    )
    assert created["source_scenario_id"] == scenario.id
    snapshot = created["source_scenario_snapshot"]
    assert snapshot["window_days"] == 30
    assert snapshot["status"] == "evaluated"
    assert snapshot["projected_result"]["autonomous_execution_rate_percent"] == 60.0

    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    started = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert started.status_code == 200, started.text
    await _add_policy_auto_runs(db, org_id, count=3)
    resp = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    done = resp.json()
    comparison = done["outcome_comparison"]
    rate_row = next(
        m for m in comparison["metrics"] if m["metric"] == "autonomous_execution_rate"
    )
    assert rate_row["projected_value"] == 60.0
    assert rate_row["observed_value"] == 100.0
    assert rate_row["variance_from_projection"] == 40.0
    assert not any("has not been evaluated" in l for l in comparison["limitations"])


@pytest.mark.asyncio
async def test_linked_unevaluated_scenario_has_no_projection(client, seeded, db):
    headers = _org_a_headers(seeded["org_a"].id)
    org_id = seeded["org_a"].id
    scenario = ServiceTransformationScenario(
        organization_id=org_id,
        name="draft-scenario",
        window_days=30,
        assumptions={"autonomous_execution_rate_target": 60.0},
        status="draft",
    )
    db.add(scenario)
    await db.commit()

    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            target_metrics={"autonomous_execution_rate": 60.0},
            source_scenario_id=scenario.id,
        ),
    )
    done = await _to_completed_org_experiment(client, headers, created["id"])
    comparison = done["outcome_comparison"]
    assert any("has not been evaluated" in l for l in comparison["limitations"])
    rate_row = next(
        m for m in comparison["metrics"] if m["metric"] == "autonomous_execution_rate"
    )
    assert rate_row["projected_value"] is None
    assert rate_row["variance_from_projection"] is None


@pytest.mark.asyncio
async def test_unknown_experiment_is_404(client, seeded):
    headers = _org_a_headers(seeded["org_a"].id)
    resp = await client.post(
        "/service-operations/experiments/999999/capture-baseline",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_target_value_projection_never_mistaken_for_observed(client, seeded, db):
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            name="projection-vs-observed",
            target_metrics={
                "autonomous_execution_rate": 60.0,
                "average_first_response_minutes": 10.0,
            },
        ),
    )
    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    started = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert started.status_code == 200, started.text
    await _add_policy_auto_runs(db, seeded["org_a"].id, count=3)
    resp = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    done = resp.json()
    observed = done["observed_outcome"]
    comparison = done["outcome_comparison"]
    assert observed["agent_runs"] == 3
    assert observed["autonomous_executions"] == 3
    fr_row = next(
        m
        for m in comparison["metrics"]
        if m["metric"] == "average_first_response_minutes"
    )
    assert fr_row["observed_value"] == 1.0
    assert fr_row["variance_from_projection"] is None
    assert done["observed_outcome"] != done["outcome_comparison"]
    assert not {"projected": True} in done["outcome_comparison"].values()


# ---------------------------------------------------------------------------
# Canonical Phase 1L SLA breach semantics (final remediation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_sla_breaches_use_canonical_escalation_evidence(
    client, seeded, db
):
    """Observed SLA breaches use persisted Phase 1L escalation evidence.

    Canonical semantics come from persisted ``ServiceEscalation`` rows
    (milestone split, stage breached, triggered in window) — never from
    ticket-deadline analytics. A ticket whose first response/resolution are
    overdue contributes zero breaches without a breached escalation row, while
    escalation rows on deadline-met carrier tickets DO count, in both the
    baseline and observed windows, with the Phase 1L per-milestone split
    exposed on the snapshot.
    """
    headers = _org_a_headers(seeded["org_a"].id)
    org_id = seeded["org_a"].id

    baseline_carriers, _ = await _seed_canonical_sla_evidence(
        db,
        organization_id=org_id,
        baseline=(1, 1),  # one first_response + one resolution breach evidence
    )
    assert baseline_carriers
    await db.commit()

    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            target_metrics={
                "total_sla_breaches": 1.0,
                "average_first_response_minutes": 10.0,
                "average_resolution_time_minutes": 100.0,
            },
        ),
    )
    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    snap = cap.json()["baseline_snapshot"]
    assert snap["first_response_sla_breaches"] == 1
    assert snap["resolution_sla_breaches"] == 1
    assert snap["total_sla_breaches"] == 2

    start = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert start.status_code == 200, start.text

    await _seed_canonical_sla_evidence(
        db,
        organization_id=org_id,
        observed=(2, 1),  # two first_response + one resolution in run window
    )
    # Ticket-deadline analytics WITHOUT escalation evidence: an overdue first
    # response and an overdue resolution, both strictly inside the run window,
    # that the canonical metric must ignore (no breached stage row).
    stamp = datetime.now(UTC)
    await _make_ticket(
        db,
        organization_id=org_id,
        created=stamp - timedelta(days=2),
        status="resolved",
        first_response_at=stamp,
        first_response_due_at=stamp - timedelta(minutes=5),
        resolved_at=stamp,
        resolution_due_at=stamp - timedelta(minutes=5),
    )
    await db.commit()

    resp = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    done = resp.json()
    observed = done["observed_outcome"]
    assert observed["first_response_sla_breaches"] == 2
    assert observed["resolution_sla_breaches"] == 1
    assert observed["total_sla_breaches"] == 3

    comparison = done["outcome_comparison"]
    sla_row = next(
        m for m in comparison["metrics"] if m["metric"] == "total_sla_breaches"
    )
    assert sla_row["baseline_value"] == 2.0
    assert sla_row["observed_value"] == 3.0
    assert sla_row["change_from_baseline"] == 1.0
    # Performance analytics stay ticket-derived (Task 4): the overdue ticket
    # contributes to the average durations, never to the breach count.
    assert isinstance(observed["average_first_response_minutes"], float)
    assert isinstance(observed["average_resolution_time_minutes"], float)


@pytest.mark.asyncio
async def test_queue_sla_breaches_canonical_and_tenant_safe(
    client, seeded, db
):
    """Queue-scope SLA breaches reuse the same canonical semantics and are
    tenant-safe: only the trusted tenant-owned queue's breached escalation rows
    count. Cross-queue rows in the same org and any cross-tenant rows are
    excluded even when they fall inside the run window."""
    headers = _org_a_headers(seeded["org_a"].id)
    org_a = seeded["org_a"].id
    org_b = seeded["org_b"].id

    queue_b = ServiceQueue(
        organization_id=org_b,
        key="xtf-queue-b-esc",
        name="Queue B",
        active=True,
        is_default=False,
    )
    db.add(queue_b)
    await db.flush()

    queue_a = ServiceQueue(
        organization_id=org_a,
        key="xtf-queue-a-esc",
        name="Queue A",
        active=True,
        is_default=False,
    )
    queue_a_other = ServiceQueue(
        organization_id=org_a,
        key="xtf-queue-a2-esc",
        name="Queue A2",
        active=True,
        is_default=False,
    )
    db.add(queue_a)
    db.add(queue_a_other)
    await db.commit()

    await _seed_canonical_sla_evidence(
        db,
        organization_id=org_a,
        queue_id=queue_a.id,
        baseline=(2, 0),  # baseline evidence in the scoped queue
    )
    await _seed_canonical_sla_evidence(
        db,
        organization_id=org_a,
        queue_id=queue_a_other.id,
        baseline=(1, 0),  # other-queue baseline evidence must stay excluded
    )
    await db.commit()

    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            scope_type=SCOPE_TYPE_QUEUE,
            scope_key="xtf-queue-a-esc",
            target_metrics={"total_sla_breaches": 0.0},
        ),
    )
    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    snap = cap.json()["baseline_snapshot"]
    assert snap["first_response_sla_breaches"] == 2
    assert snap["resolution_sla_breaches"] == 0
    assert snap["total_sla_breaches"] == 2

    start = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert start.status_code == 200, start.text

    await _seed_canonical_sla_evidence(
        db,
        organization_id=org_a,
        queue_id=queue_a.id,
        observed=(3, 1),  # scoped queue evidence: three fr + one resolution
    )
    await _seed_canonical_sla_evidence(
        db,
        organization_id=org_a,
        queue_id=queue_a_other.id,
        observed=(2, 1),  # cross-queue rows inside the window: must be excluded
    )
    await _seed_canonical_sla_evidence(
        db,
        organization_id=org_b,
        observed=(2, 1),  # cross-tenant rows: org filter excludes them
    )
    await db.commit()

    resp = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    done = resp.json()
    observed = done["observed_outcome"]
    assert observed["first_response_sla_breaches"] == 3
    assert observed["resolution_sla_breaches"] == 1
    assert observed["total_sla_breaches"] == 4

    sla_row = next(
        m
        for m in done["outcome_comparison"]["metrics"]
        if m["metric"] == "total_sla_breaches"
    )
    assert sla_row["baseline_value"] == 2.0
    assert sla_row["observed_value"] == 4.0


@pytest.mark.asyncio
async def test_sla_scenario_projection_comparison_apples_to_apples(
    client, seeded, db
):
    """Observed SLA breaches compare apples-to-apples with Phase 1N scenario
    projections: the projected ``total_sla_breaches`` is derived from the same
    canonical Phase 1L metric the observed outcome now uses, so projection
    variance is a meaningful numeric difference."""
    headers = _org_a_headers(seeded["org_a"].id)
    org_id = seeded["org_a"].id

    scenario = ServiceTransformationScenario(
        organization_id=org_id,
        name="scenario-sla-esc",
        window_days=30,
        assumptions={"sla_breach_reduction_percent": 20.0},
        status="evaluated",
        formula_version="1",
        projected_result={
            "autonomous_executions": 0,
            "autonomous_execution_rate_percent": None,
            "human_approval_required": 0,
            "human_approval_rate_percent": None,
            "knowledge_specialist_runs": 0,
            "knowledge_usage_rate_percent": None,
            "reopened_tickets": 0,
            "reopen_rate_percent": None,
            "total_sla_breaches": 5,
            "sla_breach_reduction_percent": 20.0,
            "value": {
                "estimated_minutes_saved": 0.0,
                "estimated_hours_saved": 0.0,
                "estimated_labor_savings_usd": 0.0,
                "agent_ai_cost_usd": 0.0,
                "estimated_net_savings_usd": 0.0,
                "roi_percent": None,
                "measurement_status": "measured",
            },
        },
    )
    db.add(scenario)
    await db.commit()

    created = await _create_experiment(
        client,
        headers,
        _experiment_payload(
            target_metrics={"total_sla_breaches": 1.0},
            source_scenario_id=scenario.id,
        ),
    )
    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    assert cap.json()["baseline_snapshot"]["total_sla_breaches"] == 0
    start = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert start.status_code == 200, start.text

    await _seed_canonical_sla_evidence(
        db,
        organization_id=org_id,
        observed=(2, 1),
    )
    await db.commit()

    resp = await client.post(
        f"/service-operations/experiments/{created['id']}/complete",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    done = resp.json()
    observed = done["observed_outcome"]
    assert observed["total_sla_breaches"] == 3

    sla_row = next(
        m
        for m in done["outcome_comparison"]["metrics"]
        if m["metric"] == "total_sla_breaches"
    )
    assert sla_row["baseline_value"] == 0.0
    assert sla_row["observed_value"] == 3.0
    assert sla_row["projected_value"] == 5.0
    assert sla_row["variance_from_projection"] == -2.0
    assert not any("has not been evaluated" in l for l in done["outcome_comparison"]["limitations"])


@pytest.mark.asyncio
async def test_baseline_immutable_after_start(client, seeded):
    """A captured baseline cannot be replaced once the experiment runs."""
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(client, headers)
    cap = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert cap.status_code == 200, cap.text
    baseline = cap.json()["baseline_snapshot"]

    start = await client.post(
        f"/service-operations/experiments/{created['id']}/start",
        headers=headers,
    )
    assert start.status_code == 200, start.text

    again = await client.post(
        f"/service-operations/experiments/{created['id']}/capture-baseline",
        headers=headers,
    )
    assert again.status_code == 409

    detail = await client.get(
        f"/service-operations/experiments/{created['id']}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["baseline_snapshot"] == baseline


@pytest.mark.asyncio
async def test_completed_rejects_invalid_mutation(client, seeded):
    """A completed experiment is immutable: every gateway that would rewrite
    target state or re-measure returns 409 — only archive is allowed."""
    headers = _org_a_headers(seeded["org_a"].id)
    created = await _create_experiment(client, headers)
    await _to_completed_org_experiment(client, headers, created["id"])

    for path in ("capture-baseline", "start", "complete", "cancel"):
        resp = await client.post(
            f"/service-operations/experiments/{created['id']}/{path}",
            headers=headers,
        )
        assert resp.status_code == 409, path

    archived = await client.post(
        f"/service-operations/experiments/{created['id']}/archive",
        headers=headers,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"