"""Phase 1L service transformation tenant-isolation and truthfulness tests.

The transformation analytics must be windowed, tenant-scoped and evidence
backed: every aggregate binds ``organization_id`` in SQL, NULL-org legacy rows
stay inert, windows are bounded 7/30/90 with a current-vs-previous comparison,
rates are None (never a misleading 0) when the denominator is empty, and value
realization reuses the exact ROI estimator. Two asymmetric organizations make
any global aggregation impossible to miss.
"""

# App imports are delayed until after the AUTH_* environment bootstrap +
# warnings filter: the app reads its settings at import time, so the app
# import block below carries an explicit E402 waiver (repo-wide bootstrap
# convention).

import os
import uuid
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
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

from app.core.config import reset_settings_cache, settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.rbac import OrganizationRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models.agent_run import AgentRun  # noqa: E402
from app.models.ai_request_log import AIRequestLog  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.conversation_message import ConversationMessage  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.organization_membership import (  # noqa: E402
    OrganizationMembership,
)
from app.models.service_escalation import ServiceEscalation  # noqa: E402
from app.models.service_queue import ServiceQueue  # noqa: E402
from app.models.ticket import Ticket  # noqa: E402

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-xtf-issuer"
TEST_AUDIENCE = "test-xtf-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"
USER_GAMMA = "user-gamma"
USER_NOBODY = "user-nobody"

X_TENANT = "X-CXOps-Organization-ID"

AUTO_NOTE = "Automatically approved by low-risk tool policy"

# Queue keywords used throughout the breaved/resolved seeds.
Q1_KEY = "queue-a-key"
Q2_KEY = "queue-b-key"

# Rounding parity with the service (rates are round(x*100, 2)).
DAY_MINUTES = 24 * 60


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


async def _transformation(client, headers, *, days: int | None = None) -> dict:
    url = "/service-operations/transformation"
    if days is not None:
        url = f"{url}?days={days}"
    r = await client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest_asyncio.fixture
async def seeded(db):
    """Implementation-scoped dashboard rows with deterministic timing.

    Org A (user-alpha) carries the full transformation scenario; Org B
    (user-beta) and legacy NULL-org rows are deliberately asymmetric so a
    leaked aggregate is impossible to satisfy by accident.
    """
    org_ids: list[int] = []
    ticket_ids: list[int] = []
    queue_ids: list[int] = []
    escalation_ids: list[int] = []
    conversation_ids: list[int] = []
    message_ids: list[int] = []
    run_ids: list[str] = []
    log_ids: list[int] = []

    leaked = await db.execute(
        select(Organization.id).where(Organization.name.like("xtf-%"))
    )
    leaked_ids = [row[0] for row in leaked.all()]
    for tbl, col in [
        (ConversationMessage, "organization_id"),
        (Conversation, "organization_id"),
        (ServiceEscalation, "organization_id"),
        (AgentRun, "organization_id"),
        (AIRequestLog, "organization_id"),
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

    async def make_queue(organization_id: int, *, key: str, name: str) -> ServiceQueue:
        queue = ServiceQueue(key=key, name=name, organization_id=organization_id)
        db.add(queue)
        await db.flush()
        queue_ids.append(queue.id)
        return queue

    async def make_ticket(
        organization_id: int,
        *,
        created: datetime,
        queue_id: int | None = None,
        status: str = "open",
        priority: str = "normal",
        subject_prefix: str = "t",
        resolution: tuple[datetime, datetime] | None = None,
        first_response: tuple[datetime, datetime] | None = None,
        assigned_subject: str | None = None,
    ) -> Ticket:
        resolved_at, resolution_due_at = resolution if resolution else (None, None)
        fr_at, fr_due_at = first_response if first_response else (None, None)
        ticket = Ticket(
            organization_id=organization_id,
            subject=f"{subject_prefix}-{uuid.uuid4().hex[:8]}",
            description="support case",
            status=status,
            priority=priority,
            service_queue_id=queue_id,
            assigned_subject=assigned_subject,
            created_at=created,
            first_response_at=fr_at,
            first_response_due_at=fr_due_at,
            resolved_at=resolved_at,
            resolution_due_at=resolution_due_at,
        )
        db.add(ticket)
        await db.flush()
        ticket_ids.append(ticket.id)
        return ticket

    async def make_conversation(
        organization_id: int,
        *,
        ticket: Ticket,
        channel: str,
        created: datetime,
    ) -> Conversation:
        conv = Conversation(
            organization_id=organization_id,
            ticket_id=ticket.id,
            provider="cxops",
            channel=channel,
            status="open",
            created_at=created,
        )
        db.add(conv)
        await db.flush()
        conversation_ids.append(conv.id)
        return conv

    async def add_message(
        *,
        organization_id: int,
        conversation: Conversation,
        direction: str,
        body: str,
        sent_at: datetime,
        dedupe_key: str | None = None,
        requested_by_subject: str | None = None,
    ) -> ConversationMessage:
        msg = ConversationMessage(
            organization_id=organization_id,
            conversation_id=conversation.id,
            provider="cxops",
            dedupe_key=dedupe_key,
            direction=direction,
            visibility="public",
            body=body,
            sent_at=sent_at,
            requested_by_subject=requested_by_subject,
            delivery_status="sent" if direction == "outbound" else None,
        )
        db.add(msg)
        await db.flush()
        message_ids.append(msg.id)
        if conversation.latest_message_at is None or (
            sent_at is not None
            and conversation.latest_message_at is not None
            and sent_at > conversation.latest_message_at
        ):
            conversation.latest_message_at = sent_at
        return msg

    async def add_escalation(
        *,
        organization_id: int,
        ticket: Ticket,
        milestone: str,
        stage: str,
        status: str,
        triggered_at: datetime,
        resolution_reason: str | None = None,
        resolved_at: datetime | None = None,
    ) -> ServiceEscalation:
        esc = ServiceEscalation(
            organization_id=organization_id,
            ticket_id=ticket.id,
            milestone=milestone,
            stage=stage,
            status=status,
            event_key=uuid.uuid4().hex,
            triggered_at=triggered_at,
            resolved_at=resolved_at,
            resolution_reason=resolution_reason,
            resolution_sla_cycle=0,
            source="sla_monitor",
            transition_version=1,
        )
        db.add(esc)
        await db.flush()
        escalation_ids.append(esc.id)
        return esc

    async def add_log(
        *,
        organization_id: int | None,
        feature: str,
        cost: float,
        request_id: str | None = None,
    ) -> AIRequestLog:
        log = AIRequestLog(
            organization_id=organization_id,
            request_id=request_id or uuid.uuid4().hex,
            feature=feature,
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
            estimated_cost_usd=cost,
            latency_ms=10.0,
            sources=[],
            error_message=None,
        )
        db.add(log)
        await db.flush()
        log_ids.append(log.id)
        return log

    async def add_run(
        *,
        organization_id: int | None,
        action: str,
        status: str,
        ticket: Ticket,
        workflow_path: list[str],
        requires_human_approval: bool = False,
        authorization_source: str | None = None,
        reviewer_note: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            run_id=uuid.uuid4().hex,
            ticket_id=ticket.id,
            organization_id=organization_id,
            action=action,
            reason="reason",
            response_draft="draft",
            status=status,
            requires_human_approval=requires_human_approval,
            sources=[],
            workflow_path=workflow_path,
            tool_plan=[],
            authorization_source=authorization_source,
            reviewer_note=reviewer_note,
        )
        db.add(run)
        await db.flush()
        run_ids.append(run.run_id)
        return run

    org_a = await make_org(f"xtf-a-{uuid.uuid4().hex[:8]}", subject=USER_ALPHA)
    org_b = await make_org(f"xtf-b-{uuid.uuid4().hex[:8]}", subject=USER_BETA)

    q1 = await make_queue(org_a.id, key=Q1_KEY, name="Queue A")
    q2 = await make_queue(org_a.id, key=Q2_KEY, name="Queue B")

    # ---- Org A tickets ----
    t_cur1 = await make_ticket(
        org_a.id,
        created=now - timedelta(days=1),
        queue_id=q1.id,
        priority="high",
        assigned_subject=USER_ALPHA,
        subject_prefix="cur",
    )
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=40),
        queue_id=q2.id,
        subject_prefix="prev",
    )
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=100),
        queue_id=q2.id,
        subject_prefix="old",
    )
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=6),
        first_response=(now - timedelta(days=5), now - timedelta(days=4)),
        subject_prefix="fr1",
    )
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=4),
        first_response=(now - timedelta(days=2), now - timedelta(days=3)),
        subject_prefix="fr2",
    )
    # R1..R4 resolved in window (explicit timings drive avg/median).
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=8),
        queue_id=q1.id,
        status="solved",
        resolution=(now - timedelta(days=2), now - timedelta(days=1)),
        subject_prefix="r1",
    )
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=8),
        queue_id=q1.id,
        status="solved",
        resolution=(now - timedelta(days=3), now - timedelta(days=4)),
        subject_prefix="r2",
    )
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=9),
        queue_id=q1.id,
        status="solved",
        resolution=(now - timedelta(days=4), now - timedelta(days=5)),
        subject_prefix="r3",
    )
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=9),
        queue_id=q1.id,
        status="solved",
        resolution=(now - timedelta(days=3), now - timedelta(days=2)),
        subject_prefix="r4",
    )

    await make_ticket(
        org_a.id,
        created=now - timedelta(days=6),
        queue_id=q1.id,
        subject_prefix="reopen",
    )
    t_needs = await make_ticket(
        org_a.id,
        created=now - timedelta(days=3),
        queue_id=q1.id,
        subject_prefix="needs",
    )
    t_run_a = await make_ticket(
        org_a.id,
        created=now - timedelta(days=2),
        queue_id=q1.id,
        subject_prefix="runa",
    )
    t_run_b = await make_ticket(
        org_a.id,
        created=now - timedelta(days=2),
        queue_id=q1.id,
        subject_prefix="runb",
    )

    # ---- Escalations (14+ rows, each on a dedicated solved carrier ticket) ----
    for i in range(10):
        carrier = await make_ticket(
            org_a.id,
            created=now - timedelta(days=50),
            queue_id=q1.id,
            status="solved",
            subject_prefix=f"br{i}",
        )
        await add_escalation(
            organization_id=org_a.id,
            ticket=carrier,
            milestone="resolution",
            stage="breached",
            status="open",
            triggered_at=now - timedelta(days=5),
        )
    prev_carrier = await make_ticket(
        org_a.id,
        created=now - timedelta(days=45),
        queue_id=q1.id,
        status="solved",
        subject_prefix="prev-esc",
    )
    await add_escalation(
        organization_id=org_a.id,
        ticket=prev_carrier,
        milestone="first_response",
        stage="due_soon",
        status="open",
        triggered_at=now - timedelta(days=35),
    )
    old_carrier = await make_ticket(
        org_a.id,
        created=now - timedelta(days=120),
        queue_id=q1.id,
        status="solved",
        subject_prefix="old-esc",
    )
    await add_escalation(
        organization_id=org_a.id,
        ticket=old_carrier,
        milestone="first_response",
        stage="breached",
        status="open",
        triggered_at=now - timedelta(days=95),
    )
    # Reopen trail: 5 tickets re-escalated (resolution_reason ticket_reopened)
    # whose reopen resolution landed in the current window.
    for i in range(5):
        rt = await make_ticket(
            org_a.id,
            created=now - timedelta(days=45),
            queue_id=q1.id,
            status="solved",
            subject_prefix=f"rt{i}",
        )
        await add_escalation(
            organization_id=org_a.id,
            ticket=rt,
            milestone="first_response",
            stage="breached",
            status="resolved",
            triggered_at=now - timedelta(days=35),
            resolution_reason="ticket_reopened",
            resolved_at=now - timedelta(days=2),
            )
    # The currently-open ticket that was reopened (open state, needs help).
    await make_ticket(
        org_a.id,
        created=now - timedelta(days=45),
        queue_id=q1.id,
        subject_prefix="reopened-live",
    )

    # ---- Conversations / messages (human + AI workload + channel mix) ----
    conv_email = await make_conversation(
        org_a.id,
        ticket=t_needs,
        channel="email",
        created=now - timedelta(days=3),
    )
    await add_message(
        organization_id=org_a.id,
        conversation=conv_email,
        direction="outbound",
        body="human reply",
        sent_at=now - timedelta(days=4),
        requested_by_subject=USER_ALPHA,
    )
    await add_message(
        organization_id=org_a.id,
        conversation=conv_email,
        direction="outbound",
        body="agent reply",
        sent_at=now - timedelta(days=3),
        dedupe_key="agent_run:abc123:zendesk.send_reply",
    )
    await add_message(
        organization_id=org_a.id,
        conversation=conv_email,
        direction="inbound",
        body="customer follow-up",
        sent_at=now - timedelta(days=2),
    )
    conv_web = await make_conversation(
        org_a.id,
        ticket=t_cur1,
        channel="web",
        created=now - timedelta(days=1),
    )
    await add_message(
        organization_id=org_a.id,
        conversation=conv_web,
        direction="inbound",
        body="web case",
        sent_at=now - timedelta(days=1),
    )

    # ---- Agent runs (9 runs across 2 tickets) + ROI telemetry ----
    run_a = await add_run(
        organization_id=org_a.id,
        action="respond",
        status="executed",
        ticket=t_run_a,
        workflow_path=[
            "load_ticket",
            "coordinator",
            "retrieve_knowledge",
            "knowledge_specialist",
            "decide_action",
            "action_specialist",
        ],
        requires_human_approval=True,
        authorization_source="policy_auto",
        reviewer_note=AUTO_NOTE,
    )
    await add_log(
        organization_id=org_a.id,
        feature="agent_decision",
        cost=0.001,
        request_id=f"agent-{run_a.run_id}",
    )
    await add_run(
        organization_id=org_a.id,
        action="respond",
        status="executed",
        ticket=t_run_a,
        workflow_path=["load_ticket", "coordinator", "decide_action", "action_specialist"],
        requires_human_approval=True,
        authorization_source="human_approval",
    )
    await add_run(
        organization_id=org_a.id,
        action="escalate",
        status="execution_failed",
        ticket=t_run_a,
        workflow_path=["load_ticket"],
        requires_human_approval=True,
    )
    await add_run(
        organization_id=org_a.id,
        action="escalate",
        status="rejected",
        ticket=t_run_a,
        workflow_path=["load_ticket", "coordinator", "decide_action", "action_specialist"],
        requires_human_approval=True,
    )
    await add_run(
        organization_id=org_a.id,
        action="no_action",
        status="no_action",
        ticket=t_run_a,
        workflow_path=["load_ticket", "coordinator"],
        requires_human_approval=True,
    )
    # r6..r8: plain executed runs (no autonomous source) on a second ticket.
    for _ in range(3):
        await add_run(
            organization_id=org_a.id,
            action="respond",
            status="executed",
            ticket=t_run_b,
            workflow_path=[
                "load_ticket",
                "coordinator",
                "retrieve_knowledge",
                "knowledge_specialist",
                "decide_action",
                "action_specialist",
            ],
        )
    await add_run(
        organization_id=org_a.id,
        action="human_review",
        status="pending_approval",
        ticket=t_run_b,
        workflow_path=["load_ticket", "coordinator", "decide_action"],
        requires_human_approval=True,
    )

    # ---- Org B (quiet tenant; must never see Org A rows) ----
    await make_ticket(
        org_b.id,
        created=now - timedelta(days=2),
        subject_prefix="beta",
    )
    beta_run = await add_run(
        organization_id=org_b.id,
        action="respond",
        status="executed",
        ticket=await make_ticket(
            org_b.id,
            created=now - timedelta(days=2),
            subject_prefix="beta-run",
        ),
        workflow_path=["load_ticket", "coordinator", "decide_action", "action_specialist"],
    )
    await add_log(
        organization_id=org_b.id,
        feature="agent_decision",
        cost=100.0,
        request_id=f"agent-{beta_run.run_id}",
    )

    # ---- Legacy NULL-org rows (inert for every tenant dashboard) ----
    await make_ticket(
        None,
        created=now - timedelta(days=1),
        subject_prefix="legacy",
    )
    legacy_ticket = await make_ticket(
        None,
        created=now - timedelta(days=1),
        subject_prefix="legacy-run",
    )
    await add_run(
        organization_id=None,
        action="respond",
        status="executed",
        ticket=legacy_ticket,
        workflow_path=["load_ticket"],
    )
    await add_log(organization_id=None, feature="agent_decision", cost=500.0)

    # multi-membership subject for selector tests.
    await make_org(f"xtf-gamma-{uuid.uuid4().hex[:8]}", subject=USER_GAMMA)
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

    def snapshot(org_id: int) -> dict:
        return {
            "org_id": org_id,
        }

    yield {
        "org_a": org_a,
        "org_b": org_b,
        "snapshot": snapshot,
    }

    # Broadcast teardown: children first, then memberships, then orgs.
    if message_ids:
        await db.execute(
            delete(ConversationMessage).where(ConversationMessage.id.in_(message_ids))
        )
    if conversation_ids:
        await db.execute(
            delete(Conversation).where(Conversation.id.in_(conversation_ids))
        )
    if escalation_ids:
        await db.execute(
            delete(ServiceEscalation).where(ServiceEscalation.id.in_(escalation_ids))
        )
    if log_ids:
        await db.execute(delete(AIRequestLog).where(AIRequestLog.id.in_(log_ids)))
    if run_ids:
        await db.execute(delete(AgentRun).where(AgentRun.run_id.in_(run_ids)))
    if ticket_ids:
        await db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
    if queue_ids:
        await db.execute(delete(ServiceQueue).where(ServiceQueue.id.in_(queue_ids)))
    if org_ids:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xtf_a_response_shape_and_structure(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    assert data["window"]["days"] == 30
    for section in (
        "service_volume",
        "service_performance",
        "sla",
        "ai_adoption",
        "specialist_usage",
        "human_workload",
        "value_realization",
    ):
        assert section in data, section
    assert isinstance(data["queue_breakdown"], list)
    assert isinstance(data["channel_breakdown"], list)
    assert isinstance(data["comparisons"], dict)
    assert isinstance(data["opportunity_signals"], list)

    # No fabricated metrics are ever exposed.
    flat = {k.lower() for k in data["comparisons"]}
    for banned in ("fcr", "csat", "sentiment", "transformation_score", "overall"):
        assert not any(banned in key for key in flat), banned


@pytest.mark.asyncio
async def test_xtf_b_volume_and_performance_are_window_exact(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    vol = data["service_volume"]
    assert vol["tickets_created"] == 11
    assert vol["tickets_resolved"] == 4
    assert vol["currently_open"] == 10

    perf = data["service_performance"]
    assert perf["average_first_response_minutes"] == pytest.approx(2160.0, abs=1e-9)
    assert perf["median_first_response_minutes"] == pytest.approx(2160.0, abs=1e-9)
    assert perf["average_resolution_time_minutes"] == pytest.approx(7920.0, abs=1e-9)
    assert perf["median_resolution_time_minutes"] == pytest.approx(7920.0, abs=1e-9)


@pytest.mark.asyncio
async def test_xtf_c_sla_counts_and_rates(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    sla = data["sla"]
    assert sla["escalation_count"] == 10  # 10 breved resolution rows in window
    assert sla["first_response_sla_breaches"] == 0
    assert sla["resolution_sla_breaches"] == 10
    assert sla["total_sla_breaches"] == 10
    assert sla["escalation_rate"] == pytest.approx(round(10 / 11 * 100, 2), abs=1e-9)
    # Reopen trail: 5 reopened tickets in window.
    assert sla["reopened_tickets"] == 5
    assert sla["reopen_rate"] == pytest.approx(round(5 / 4 * 100, 2), abs=1e-9)

    # Previous-window escalation cohort: must NOT absorb current-window rows.
    # Rows triggered 35d ago (1 due_soon + 5 reopened) are the whole previous
    # cohort for a 30d window; the 10 rows triggered 5d ago stay current-only.
    comps = data["comparisons"]
    assert comps["escalation_count"]["current"] == 10
    assert comps["escalation_count"]["previous"] == 6


@pytest.mark.asyncio
async def test_xtf_d_ai_adoption_and_specialists(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    ai = data["ai_adoption"]
    assert ai["agent_runs"] == 9
    assert ai["tickets_analyzed_by_ai"] == 2
    assert ai["autonomous_executions"] == 1
    assert ai["human_approval_required"] == 5
    assert ai["human_approved"] == 1
    assert ai["human_rejected"] == 1
    assert ai["successful_agent_executions"] == 5
    assert ai["failed_agent_executions"] == 1
    assert ai["no_action_runs"] == 1
    assert ai["autonomous_execution_rate"] == pytest.approx(
        round(1 / 9 * 100, 2), abs=1e-9
    )
    assert ai["human_approval_rate"] == pytest.approx(
        round(5 / 9 * 100, 2), abs=1e-9
    )
    assert ai["execution_success_rate"] == pytest.approx(
        round(5 / 6 * 100, 2), abs=1e-9
    )

    sp = data["specialist_usage"]
    assert sp["coordinator_runs"] == 8
    assert sp["knowledge_specialist_runs"] == 4
    assert sp["action_specialist_runs"] == 7
    assert sp["pure_action_route_rate"] == pytest.approx(round(3 / 9 * 100, 2), abs=1e-9)
    assert sp["invalid_specialist_path_count"] == 2


@pytest.mark.asyncio
async def test_xtf_e_human_workload_and_value_realization(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    work = data["human_workload"]
    assert work["human_messages_sent"] == 1
    assert work["ai_executed_replies"] == 1

    value = data["value_realization"]
    # Windowed ROI reuses the estimator: 1 autonomous instrumented execution in
    # the current window -> minutes_saved == rate, previous window has none.
    assert value["estimated_minutes_saved"] == pytest.approx(
        settings.minutes_saved_per_autonomous_execution, abs=1e-9
    )
    assert value["roi_percent"] is None  # insufficient sample, never fabricated
    assert value["sample_size_sufficient"] is False
    assert value["measurement_status"] == "insufficient_sample"


@pytest.mark.asyncio
async def test_xtf_f_queue_and_channel_breakdowns(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    queues = {q["queue_key"]: q for q in data["queue_breakdown"]}
    assert set(queues) == {Q1_KEY, Q2_KEY}
    q1 = queues[Q1_KEY]
    assert q1["open_tickets"] == 6
    assert q1["needs_response"] == 2
    assert q1["breached"] == 11  # 10 window carriers + 95d-old unresolved breach (current state)
    assert q1["due_soon"] == 1
    assert q1["priority_urgent_high"] == 1
    assert q1["assigned_tickets"] == 1
    assert q1["resolved_in_window"] == 4
    assert q1["agent_runs"] == 9
    assert q1["autonomous_executions"] == 1

    channels = {c["channel"]: c for c in data["channel_breakdown"]}
    assert set(channels) == {"email", "web"}
    assert channels["email"]["conversation_count"] == 1
    assert channels["email"]["message_count"] == 3
    assert channels["email"]["percentage"] == 50.0
    assert channels["web"]["message_count"] == 1


@pytest.mark.asyncio
async def test_xtf_g_comparisons_previous_zero_percent_none(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    comps = data["comparisons"]
    created = comps["tickets_created"]
    assert created["current"] == 11
    assert created["previous"] == 18
    assert created["absolute_change"] == -7
    assert created["percent_change"] == pytest.approx(
        round((-7 / 18) * 100, 2), abs=1e-9
    )

    resolved = comps["tickets_resolved"]
    assert resolved["current"] == 4
    assert resolved["previous"] == 0
    assert resolved["percent_change"] is None  # no division by zero, no 0%
    assert resolved["absolute_change"] == 4


@pytest.mark.asyncio
async def test_xtf_h_opportunity_signals_deterministic(client, seeded):
    org_a = seeded["org_a"]
    data = await _transformation(
        client, _auth_headers(USER_ALPHA, org_a.id), days=30
    )

    signals = [
        (s["signal"], s["scope_type"], s["scope_key"]) for s in data["opportunity_signals"]
    ]
    assert ("sla_pressure", "queue", Q1_KEY) in signals
    assert ("ai_adoption", "organization", "organization") in signals
    assert ("ai_adoption", "queue", Q1_KEY) in signals
    assert ("knowledge_utilization", "organization", "organization") in signals
    assert ("approval_backlog", "organization", "organization") in signals
    assert ("reopen_risk", "organization", "organization") in signals

    # Deterministic ordering + bounded evidence, no people/PII.
    for s in data["opportunity_signals"]:
        assert s["scope_type"] in ("queue", "organization")
        assert s["suggested_focus"]
        assert isinstance(s["evidence"], dict)


@pytest.mark.asyncio
async def test_xtf_i_day_windows_7_30_90_boundaries(client, seeded):
    org_a = seeded["org_a"]
    headers = _auth_headers(USER_ALPHA, org_a.id)

    d7 = await _transformation(client, headers, days=7)
    d30 = await _transformation(client, headers, days=30)
    d90 = await _transformation(client, headers, days=90)

    assert d7["window"]["days"] == 7
    assert d90["window"]["days"] == 90

    # Current window created counts widen with the window.
    assert d7["service_volume"]["tickets_created"] == 7
    assert d30["service_volume"]["tickets_created"] == 11
    assert d90["service_volume"]["tickets_created"] > d30["service_volume"]["tickets_created"]

    # Escalations: only the 10 current breached rows for 7d; + prev/old for 90d.
    assert d7["sla"]["escalation_count"] == 10
    assert d30["sla"]["escalation_count"] == 10
    assert d90["sla"]["escalation_count"] == 16


@pytest.mark.asyncio
async def test_xtf_j_invalid_days_rejected_fail_closed(client, seeded):
    org_a = seeded["org_a"]
    headers = _auth_headers(USER_ALPHA, org_a.id)
    for bad_days in (1, 15, 45, 91, 3650):
        r = await client.get(
            f"/service-operations/transformation?days={bad_days}",
            headers=headers,
        )
        assert r.status_code == 422, (bad_days, r.text)


@pytest.mark.asyncio
async def test_xtf_k_tenant_isolation_and_null_org_inert(client, seeded):
    org_a = seeded["org_a"]
    org_b = seeded["org_b"]
    headers_b = _auth_headers(USER_BETA, org_b.id)

    data = await _transformation(client, headers_b, days=30)
    assert data["service_volume"]["tickets_created"] == 2
    assert data["service_volume"]["currently_open"] == 2
    assert data["ai_adoption"]["agent_runs"] == 1
    assert data["ai_adoption"]["autonomous_executions"] == 0
    assert data["queue_breakdown"] == []
    # Legacy NULL-org rows never enter either tenant's aggregates.

    a = await _transformation(client, _auth_headers(USER_ALPHA, org_a.id), days=90)
    assert a["ai_adoption"]["agent_runs"] == 9


@pytest.mark.asyncio
async def test_xtf_l_missing_membership_and_forged_selector(client, seeded):
    org_b = seeded["org_b"]

    r = await client.get(
        "/service-operations/transformation?days=30",
        headers=_auth_headers(USER_NOBODY),
    )
    assert r.status_code == 403

    r = await client.get(
        "/service-operations/transformation?days=30",
        headers=_auth_headers(USER_ALPHA, org_b.id),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_xtf_m_multi_membership_selector(client, seeded):
    org_a = seeded["org_a"]
    org_b = seeded["org_b"]

    r = await client.get(
        "/service-operations/transformation?days=7",
        headers=_auth_headers(USER_GAMMA),
    )
    assert r.status_code == 409

    a = await _transformation(
        client, _auth_headers(USER_GAMMA, org_a.id), days=7
    )
    assert a["service_volume"]["tickets_created"] == 7

    b = await _transformation(
        client, _auth_headers(USER_GAMMA, org_b.id), days=7
    )
    assert b["service_volume"]["currently_open"] == 2


@pytest.mark.asyncio
async def test_xtf_n_no_unscoped_aggregation_caller(client, seeded):
    root = Path(__file__).resolve().parent.parent
    supervised = [
        root / "app" / "api" / "routes" / "service_operations.py",
        root / "app" / "services" / "service_transformation_service.py",
        root / "app" / "repositories" / "service_transformation_repository.py",
    ]
    for path in supervised:
        source = path.read_text()
        assert "_unscoped" not in source, f"{path.name} must not call *_unscoped"
        assert "organization_id" in source, (
            f"{path.name} must always scope by organization_id"
        )