"""Phase 1P.7 — live-pilot summary endpoint (read-only, tenant-scoped).

This suite proves the operations/monitoring surface over the real HTTP route
and the real service/DB seams:

- the summary endpoint is capability-gated on TICKET_READ and derives the
  tenant exclusively from the principal's memberships (a client-supplied org
  is never accepted, and absent a membership the request is denied)
- the response window is a closed set (24h / 7d); anything else is 422
- every aggregate (queue, window activity, RAG, grounded replies, integration
  jobs, autonomous executions) is tenant-scoped and never leaks a foreign
  tenant's rows
- queue counts and the waiting-health signal exclude expired sessions, and the
  health signal measures only sessions waiting for their first staff
  assignment (``human_requested``)
- the RAG latency average is computed over successful rows only, so the
  synthetic failure marker rows never drag it toward zero
- the response shape carries only bounded counts/timestamps/costs — no session
  tokens, keys, message text, or other PII
- the RAG failure path in the widget service records a durable, bounded error
  row (feature='public_chat', status='error') so failures are observable
- the endpoint never mutates data

No LLM or knowledge-search is exercised: fixtures insert deterministic rows
directly through the ORM.
"""

import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, func, select, update

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-p1p7-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "cxops-unit-test"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache, settings
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.agent_action_event import AgentActionEvent
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.business_action import BusinessAction
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
    OrganizationRole,
)
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.models.ticket import Ticket
from app.services.public_chat_service import (
    RAG_FAILURE_PLACEHOLDER_QUESTION,
    hash_digest,
    public_chat_service,
)

ALLOWED_ORIGIN = "https://widget.example.test"
TENANT_HEADER = "x-cxops-organization-id"

P1P7_ORG_PREFIX = "p1p7-test-"

# Tables with an ``organization_id`` column are purged by org. Construction
# rows that lack one are handled explicitly in ``_purge_org_tables``.
_PURGE_TABLES = [
    "ai_request_logs",
    "public_chat_sessions",
    "public_chat_configurations",
    "business_actions",
    "conversation_messages",
    "integration_jobs",
    "agent_runs",
    "conversations",
    "tickets",
    "organization_memberships",
]
_PURGE_NAMESPACE = _PURGE_TABLES + ["agent_action_events", "organizations"]


# ----------------------------------------------------------------------
# Fixtures and namespace isolation
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "z" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": "test-p1p7-issuer",
        "AUTH_JWT_AUDIENCE": "cxops-unit-test",
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield


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


@pytest.fixture(scope="module", autouse=True)
def _p1p7_namespace_isolated():
    asyncio.run(_purge_p1p7_namespace())
    yield
    asyncio.run(_assert_p1p7_namespace_clean())


async def _purge_p1p7_namespace() -> list[int]:
    async with AsyncSessionLocal() as session:
        org_ids = [
            org_id
            for (org_id,) in await session.execute(
                select(Organization.id).where(
                    Organization.name.like(f"{P1P7_ORG_PREFIX}%")
                )
            )
        ]
        await _purge_org_tables(session, org_ids, delete_orgs=True)
        return org_ids


async def _assert_p1p7_namespace_clean() -> None:
    async with AsyncSessionLocal() as session:
        leftover = await session.execute(
            select(Organization.id).where(
                Organization.name.like(f"{P1P7_ORG_PREFIX}%")
            )
        )
        assert not leftover.all(), "Phase 1P.7 namespace leaked organizations"


async def _purge_org_tables(
    session,
    org_ids: list[int],
    *,
    delete_orgs: bool = False,
) -> None:
    if not org_ids:
        return
    from app.models.base import Base

    # agent_action_events has no organization_id column; its parent agent runs
    # do. Purge them first so no events outlive their runs.
    await session.execute(
        delete(AgentActionEvent).where(
            AgentActionEvent.agent_run_id.in_(
                select(AgentRun.id).where(AgentRun.organization_id.in_(org_ids))
            )
        )
    )
    for table_name in _PURGE_TABLES:
        table = Base.metadata.tables.get(table_name)
        if table is not None and "organization_id" in table.columns:
            await session.execute(
                delete(table).where(table.c.organization_id.in_(org_ids))
            )
    if delete_orgs:
        await session.execute(
            delete(Organization).where(Organization.id.in_(org_ids))
        )
    await session.commit()


@pytest_asyncio.fixture
async def org_scope(db):
    org_ids: list[int] = []

    async def make() -> Organization:
        org = Organization(name=f"{P1P7_ORG_PREFIX}{uuid.uuid4().hex[:10]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        await db.commit()
        return org

    yield make

    await _purge_org_tables(db, org_ids)
    await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _build_token(*, sub: str) -> str:
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": os.environ["AUTH_JWT_ISSUER"],
        "aud": os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + 3600,
        "iat": now,
    }
    return jwt.encode(
        payload,
        os.environ["AUTH_JWT_SECRET"],
        algorithm="HS256",
    )


async def _add_membership(
    db,
    subject: str,
    organization_id: int,
    *,
    role: OrganizationRole = OrganizationRole.OWNER,
) -> None:
    db.add(
        OrganizationMembership(
            subject=subject,
            organization_id=organization_id,
            role=role,
        )
    )
    await db.commit()


def _staff_headers(subject: str, organization_id: int) -> dict:
    return {
        "Authorization": f"Bearer {_build_token(sub=subject)}",
        TENANT_HEADER: str(organization_id),
    }


async def _get_or_create_config(
    db,
    org: Organization,
) -> PublicChatConfiguration:
    """A tenant owns exactly one widget configuration (unique organization_id).

    Reuse it across the sessions a test creates within one tenant.
    """
    existing = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    config = PublicChatConfiguration(
        organization_id=org.id,
        public_widget_key_hash=hash_digest(f"pk_live_{secrets.token_hex(20)}"),
        display_name="Acme Support",
        welcome_message="Hi!",
        allowed_origins=[ALLOWED_ORIGIN],
        enabled=True,
        grounded_auto_reply_enabled=True,
        theme_token="midnight",
        max_message_length=4000,
        max_messages_per_minute=20,
        session_ttl_hours=24,
    )
    db.add(config)
    await db.flush()
    return config


async def _make_widget_session(
    db,
    org: Organization,
    *,
    status: str = "ai_active",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    closed_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict:
    """Insert a deterministic public-chat session (with config/conversation/ticket)."""
    config = await _get_or_create_config(db, org)

    conversation = Conversation(
        organization_id=org.id,
        provider="public_chat",
        channel="public_chat",
        status="open",
    )
    db.add(conversation)
    await db.flush()

    ticket = Ticket(
        organization_id=org.id,
        subject="Widget inquiry",
        description=".",
        status="open",
        priority="medium",
        source="public-chat-widget",
    )
    db.add(ticket)
    await db.flush()

    session = PublicChatSession(
        organization_id=org.id,
        configuration_id=config.id,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        token_hash=hash_digest(secrets.token_hex(32)),
        status=status,
        expires_at=(
            expires_at
            if expires_at is not None
            else datetime.now(UTC) + timedelta(hours=24)
        ),
        closed_at=closed_at,
    )
    db.add(session)
    await db.flush()

    if created_at is not None:
        await db.execute(
            update(PublicChatSession)
            .where(PublicChatSession.id == session.id)
            .values(created_at=created_at)
            .execution_options(synchronize_session=False)
        )
    if updated_at is not None:
        await db.execute(
            update(PublicChatSession)
            .where(PublicChatSession.id == session.id)
            .values(updated_at=updated_at)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    await db.refresh(session)
    return {
        "config": config,
        "conversation": conversation,
        "ticket": ticket,
        "session": session,
    }


async def _make_message(
    db,
    conversation: Conversation,
    *,
    direction: str = "inbound",
    created_at: datetime | None = None,
) -> None:
    message = ConversationMessage(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        provider="public_chat",
        direction=direction,
        visibility="visible",
        body="I need help with my order.",
    )
    db.add(message)
    await db.flush()
    if created_at is not None:
        await db.execute(
            update(ConversationMessage)
            .where(ConversationMessage.id == message.id)
            .values(created_at=created_at)
            .execution_options(synchronize_session=False)
        )
    await db.commit()


async def _make_run(
    db,
    ticket: Ticket,
    org_id: int,
    *,
    status: str = "completed",
    authorization_source: str | None = None,
    executed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> AgentRun:
    run = AgentRun(
        run_id=uuid.uuid4().hex,
        ticket_id=ticket.id,
        organization_id=org_id,
        action="respond",
        reason="Public-chat grounded auto-reply.",
        response_draft=None,
        reviewer_note=None,
        status=status,
        authorization_source=authorization_source,
        executed_at=executed_at,
        sources=[],
        workflow_path=["public_chat_send_message"],
        tool_plan=[],
    )
    db.add(run)
    await db.flush()
    if created_at is not None:
        await db.execute(
            update(AgentRun)
            .where(AgentRun.id == run.id)
            .values(created_at=created_at)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    await db.refresh(run)
    return run


async def _make_grounded_event(db, run: AgentRun) -> None:
    db.add(
        AgentActionEvent(
            agent_run_id=run.id,
            event_type="public_grounded_auto_reply",
            actor="system",
            note="Grounded answer sent to customer.",
            event_data={},
        )
    )
    await db.commit()


async def _make_rag_row(
    db,
    org_id: int,
    *,
    feature: str = "public_chat",
    status: str = "success",
    grounded: bool | None = None,
    latency_ms: float = 100.0,
    cost_usd: float = 0.0,
    best_similarity: float | None = 0.75,
) -> None:
    db.add(
        AIRequestLog(
            organization_id=org_id,
            request_id=uuid.uuid4().hex,
            feature=feature,
            model=settings.chat_model,
            status=status,
            question="What is the status of my order?",
            answer=None if status != "success" else "Your order shipped.",
            grounded=bool(grounded),
            llm_called=bool(grounded),
            retrieval_count=3,
            best_similarity=best_similarity,
            latency_ms=latency_ms,
            estimated_cost_usd=cost_usd,
            error_message=(
                None if status == "success" else "public_chat_grounded_auto_reply_failed"
            ),
        )
    )
    await db.commit()


async def _make_integration_job(
    db,
    org_id: int,
    run_id: str,
    *,
    job_type: str = "agent.execute",
) -> None:
    db.add(
        IntegrationJob(
            dedupe_key=f"p1p7-{uuid.uuid4().hex}",
            job_type=job_type,
            organization_id=org_id,
            payload={"run_id": run_id},
        )
    )
    await db.commit()


async def _make_business_action(
    db,
    org_id: int,
    ticket: Ticket,
    conversation: Conversation,
    *,
    run_id: str | None,
) -> None:
    db.add(
        BusinessAction(
            organization_id=org_id,
            ticket_id=ticket.id,
            conversation_id=conversation.id,
            run_id=run_id,
            request_type="customer.send_reply",
            status="completed",
            reference_id="A1-77",
            dedupe_key=f"p1p7-{uuid.uuid4().hex}",
        )
    )
    await db.commit()


async def _get_summary(client, subject: str, org: Organization, *, window: str = "24h"):
    response = await client.get(
        "/staff/public-chat/summary",
        params={"window": window},
        headers=_staff_headers(subject, org.id),
    )
    return response


# ----------------------------------------------------------------------
# Authorization and tenant resolution
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_requires_ticket_read(db, org_scope, client):
    org = await org_scope()
    subject = f"staff-{uuid.uuid4().hex[:8]}"
    response = await client.get(
        "/staff/public-chat/summary",
        headers=_staff_headers(subject, org.id),
    )
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_summary_ignores_org_query_param_and_uses_principal(db, org_scope, client):
    org_a = await org_scope()
    org_b = await org_scope()
    await _make_widget_session(db, org_a)
    for _ in range(3):
        await _make_widget_session(db, org_b)

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org_a.id)

    # The route accepts no organization_id parameter; spoofing one must not
    # rebind the tenant. The dashboard reflects the authorized tenant only.
    response = await client.get(
        "/staff/public-chat/summary",
        params={"window": "24h", "organization_id": org_b.id},
        headers=_staff_headers(subject, org_a.id),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tenant_id"] == org_a.id
    assert data["window_summary"]["sessions_created"] == 1


@pytest.mark.asyncio
async def test_summary_is_tenant_scoped(db, org_scope, client):
    org_a = await org_scope()
    org_b = await org_scope()

    bundle = await _make_widget_session(db, org_a)
    await _make_message(db, bundle["conversation"])
    for _ in range(4):
        await _make_widget_session(db, org_b)

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org_a.id)

    response = await _get_summary(client, subject, org_a)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tenant_id"] == org_a.id
    assert data["config"]["widget_enabled"] is True
    assert data["config"]["theme_token"] == "midnight"
    assert data["config"]["allowed_origin_count"] == 1
    assert data["window_summary"]["sessions_created"] == 1
    assert data["window_summary"]["customer_messages"] == 1
    assert data["queue"]["ai_active"] == 1

    # A foreign tenant's four sessions never leak into A's summary.
    assert data["window_summary"]["sessions_created"] != 5


@pytest.mark.asyncio
async def test_foreign_membership_is_denied(db, org_scope, client):
    org_a = await org_scope()
    org_b = await org_scope()
    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org_a.id)

    # The principal is not a member of B: selecting B as the tenant is denied
    # even though the token is otherwise valid.
    response = await client.get(
        "/staff/public-chat/summary",
        headers=_staff_headers(subject, org_b.id),
    )
    assert response.status_code == 403, response.text


# ----------------------------------------------------------------------
# Window validation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_window_is_a_closed_set(db, org_scope, client):
    org = await org_scope()
    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    for window in ("24h", "7d"):
        response = await _get_summary(client, subject, org, window=window)
        assert response.status_code == 200, response.text
        assert response.json()["window"] == window

    for window in ("30d", "720h", "", "7D"):
        response = await _get_summary(client, subject, org, window=window)
        assert response.status_code == 422, f"{window!r} must be rejected"


# ----------------------------------------------------------------------
# Aggregates
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_summary_respects_window_boundaries(db, org_scope, client):
    org = await org_scope()
    now = datetime.now(UTC)
    old_dt = now - timedelta(hours=72)

    fresh = await _make_widget_session(db, org)
    await _make_message(db, fresh["conversation"])
    await _make_message(db, fresh["conversation"])
    old = await _make_widget_session(db, org, created_at=old_dt)
    await _make_message(db, old["conversation"], created_at=old_dt)

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    day = (await _get_summary(client, subject, org, window="24h")).json()
    assert day["window_summary"]["sessions_created"] == 1
    assert day["window_summary"]["customer_messages"] == 2

    week = (await _get_summary(client, subject, org, window="7d")).json()
    assert week["window_summary"]["sessions_created"] == 2
    assert week["window_summary"]["customer_messages"] == 3


@pytest.mark.asyncio
async def test_queue_summary_and_health(db, org_scope, client):
    org = await org_scope()
    now = datetime.now(UTC)

    await _make_widget_session(
        db, org, status="human_requested", updated_at=now - timedelta(minutes=30)
    )
    await _make_widget_session(
        db, org, status="human_assigned", updated_at=now - timedelta(minutes=2)
    )
    await _make_widget_session(db, org, status="ai_active")

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    data = (await _get_summary(client, subject, org)).json()
    assert data["queue"] == {
        "human_requested": 1,
        "human_assigned": 1,
        "ai_active": 1,
        "active_total": 3,
    }
    assert data["queue_health"]["health"] == "attention"
    assert data["queue_health"]["oldest_waiting_since"] is not None
    assert data["queue_health"]["oldest_waiting_minutes"] >= 15


@pytest.mark.asyncio
async def test_queue_health_is_normal_when_empty(db, org_scope, client):
    org = await org_scope()
    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    data = (await _get_summary(client, subject, org)).json()
    assert data["queue"] == {
        "human_requested": 0,
        "human_assigned": 0,
        "ai_active": 0,
        "active_total": 0,
    }
    assert data["queue_health"]["health"] == "normal"
    assert data["queue_health"]["oldest_waiting_since"] is None
    assert data["queue_health"]["oldest_waiting_minutes"] is None


@pytest.mark.asyncio
async def test_queue_counts_exclude_expired_sessions(db, org_scope, client):
    """Expired sessions are nobody's pending work and never enter the queue."""
    org = await org_scope()
    now = datetime.now(UTC)
    expired = now - timedelta(minutes=5)

    await _make_widget_session(db, org, status="ai_active", expires_at=expired)
    await _make_widget_session(db, org, status="human_requested", expires_at=expired)
    await _make_widget_session(db, org, status="human_assigned", expires_at=expired)
    await _make_widget_session(db, org, status="ai_active")

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    data = (await _get_summary(client, subject, org)).json()
    assert data["queue"] == {
        "human_requested": 0,
        "human_assigned": 0,
        "ai_active": 1,
        "active_total": 1,
    }
    assert data["queue_health"]["health"] == "normal"
    assert data["queue_health"]["oldest_waiting_since"] is None


@pytest.mark.asyncio
async def test_queue_health_ignores_human_assigned(db, org_scope, client):
    """An assigned session is being handled; its age is not waiting time."""
    org = await org_scope()
    now = datetime.now(UTC)

    await _make_widget_session(
        db, org, status="human_assigned", updated_at=now - timedelta(minutes=75)
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    data = (await _get_summary(client, subject, org)).json()
    assert data["queue_health"]["health"] == "normal"
    assert data["queue_health"]["oldest_waiting_since"] is None
    assert data["queue_health"]["oldest_waiting_minutes"] is None


@pytest.mark.asyncio
async def test_queue_health_is_attention_only_for_live_human_requested(
    db, org_scope, client
):
    """Attention fires only for a live human_requested entry, tenant-scoped."""
    org = await org_scope()
    foreign = await org_scope()
    now = datetime.now(UTC)

    # An expired human_requested session is nobody's pending work.
    await _make_widget_session(
        db,
        org,
        status="human_requested",
        updated_at=now - timedelta(minutes=60),
        expires_at=now - timedelta(minutes=1),
    )
    # A foreign tenant's live long-waiting human_requested session never trips
    # this tenant's health.
    await _make_widget_session(
        db,
        foreign,
        status="human_requested",
        updated_at=now - timedelta(minutes=60),
    )

    # This tenant's live human_requested entry does trip attention.
    await _make_widget_session(
        db,
        org,
        status="human_requested",
        updated_at=now - timedelta(minutes=20),
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    data = (await _get_summary(client, subject, org)).json()
    assert data["queue_health"]["health"] == "attention"
    assert data["queue_health"]["oldest_waiting_minutes"] >= 15


@pytest.mark.asyncio
async def test_grounded_replies_and_closed_count(db, org_scope, client):
    org = await org_scope()
    now = datetime.now(UTC)

    open_bundle = await _make_widget_session(db, org)
    run = await _make_run(db, open_bundle["ticket"], org.id)
    await _make_grounded_event(db, run)

    await _make_widget_session(
        db, org, status="closed", closed_at=now - timedelta(hours=1)
    )
    # Closed outside the window must not leak into 24h.
    await _make_widget_session(
        db, org, status="closed", closed_at=now - timedelta(days=4)
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    data = (await _get_summary(client, subject, org)).json()
    assert data["window_summary"]["grounded_public_auto_replies"] == 1
    assert data["window_summary"]["sessions_closed"] == 1

    updated_at_old = (await _get_summary(client, subject, org, window="7d")).json()
    assert updated_at_old["window_summary"]["sessions_closed"] == 2


@pytest.mark.asyncio
async def test_rag_summary_is_feature_and_tenant_scoped(db, org_scope, client):
    org_a = await org_scope()
    org_b = await org_scope()

    await _make_rag_row(
        db, org_a.id, grounded=True, latency_ms=120.0, cost_usd=0.01, best_similarity=0.81
    )
    await _make_rag_row(
        db,
        org_a.id,
        status="error",
        latency_ms=0.0,
        best_similarity=None,
    )
    # A shared-label row predates the widget's dedicated feature and must not
    # be attributed to public chat.
    await _make_rag_row(db, org_a.id, feature="rag_answer", grounded=True)
    # A foreign tenant's rows never leak.
    await _make_rag_row(db, org_b.id, grounded=True, latency_ms=999.0)

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org_a.id)

    rag = (await _get_summary(client, subject, org_a)).json()["rag"]
    assert rag["rag_requests"] == 2
    assert rag["rag_grounded"] == 1
    assert rag["rag_errors"] == 1
    # The synthetic failure marker's 0.0 latency must not drag the average.
    assert rag["avg_rag_latency_ms"] == 120.0
    assert rag["avg_best_similarity"] == pytest.approx(0.81)
    assert rag["estimated_ai_cost_usd"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_rag_latency_ignores_error_rows(db, org_scope, client):
    org = await org_scope()

    # Only error rows in the window: the synthetic failure markers carry
    # latency_ms=0.0 and must never be averaged in. With no successful rows the
    # average is 0.0 while the error count stays intact.
    await _make_rag_row(
        db, org.id, status="error", latency_ms=0.0, best_similarity=None
    )
    await _make_rag_row(
        db, org.id, status="error", latency_ms=0.0, best_similarity=None
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    rag = (await _get_summary(client, subject, org)).json()["rag"]
    assert rag["rag_errors"] == 2
    assert rag["avg_rag_latency_ms"] == 0.0


@pytest.mark.asyncio
async def test_durable_rag_error_row_records_failure(db, org_scope):
    org = await org_scope()

    await public_chat_service._record_rag_failure(
        db,
        organization_id=org.id,
    )
    await db.commit()

    rows = (
        await db.execute(
            select(AIRequestLog).where(
                AIRequestLog.organization_id == org.id,
                AIRequestLog.feature == "public_chat",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    (row,) = rows
    assert row.status == "error"
    assert row.grounded is False
    assert row.llm_called is False
    assert row.error_message == "public_chat_grounded_auto_reply_failed"
    assert row.model == settings.chat_model
    assert row.request_id
    assert row.created_at is not None
    # The synthetic marker never duplicates the customer's raw question text.
    assert row.question == RAG_FAILURE_PLACEHOLDER_QUESTION
    assert "refund" not in row.question


@pytest.mark.asyncio
async def test_autonomous_counts_only_policy_auto_public_chat_runs(
    db, org_scope, client
):
    org = await org_scope()
    foreign = await org_scope()
    bundle = await _make_widget_session(db, org)
    foreign_bundle = await _make_widget_session(db, foreign)

    # policy_auto run that executed on a public-chat ticket => counted.
    await _make_run(
        db,
        bundle["ticket"],
        org.id,
        status="executed",
        authorization_source="policy_auto",
        executed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # policy_auto run that attempted execution and failed => counted via the
    # created_at fallback (execution_failed runs do not populate executed_at).
    await _make_run(
        db,
        bundle["ticket"],
        org.id,
        status="execution_failed",
        authorization_source="policy_auto",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    # A human-approved run that executed on a public-chat ticket is NOT
    # autonomous even though it carries a run_id.
    await _make_run(
        db,
        bundle["ticket"],
        org.id,
        status="executed",
        authorization_source="human_approval",
        executed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # A policy_auto run on a ticket with no public-chat session => not counted.
    non_pc_ticket = Ticket(
        organization_id=org.id,
        subject="Pager alert",
        description=".",
        status="open",
        priority="medium",
        source="email",
    )
    db.add(non_pc_ticket)
    await db.commit()
    await _make_run(
        db,
        non_pc_ticket,
        org.id,
        status="executed",
        authorization_source="policy_auto",
        executed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # A policy_auto run on a public-chat ticket outside this window is not
    # counted for the 24h query.
    await _make_run(
        db,
        bundle["ticket"],
        org.id,
        status="executed",
        authorization_source="policy_auto",
        executed_at=datetime.now(UTC) - timedelta(hours=72),
    )
    # A foreign tenant's policy_auto public-chat run never leaks.
    await _make_run(
        db,
        foreign_bundle["ticket"],
        foreign.id,
        status="executed",
        authorization_source="policy_auto",
        executed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # A durable business action referencing a run id is not, by itself, an
    # autonomous execution: only a policy_auto attempt on the run counts.
    await _make_business_action(
        db, org.id, bundle["ticket"], bundle["conversation"], run_id=uuid.uuid4().hex
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    safety = (await _get_summary(client, subject, org)).json()["safety"]
    assert safety["autonomous_public_chat_executions"] == 2


@pytest.mark.asyncio
async def test_integration_jobs_require_agent_execute_job_type(db, org_scope, client):
    org = await org_scope()
    foreign = await org_scope()
    bundle = await _make_widget_session(db, org)
    tracked_run = await _make_run(
        db, bundle["ticket"], org.id, status="executed", authorization_source="policy_auto"
    )

    # agent.execute job correlating to a tenant-scoped public-chat run => counted.
    await _make_integration_job(db, org.id, tracked_run.run_id, job_type="agent.execute")
    # Any other job type (e.g. webhook delivery) with an equivalent run_id
    # payload is not an agent-execution job => not counted.
    await _make_integration_job(
        db, org.id, tracked_run.run_id, job_type="webhook.delivery"
    )
    # An agent.execute job whose run_id resolves to nothing known => not counted.
    await _make_integration_job(db, org.id, uuid.uuid4().hex, job_type="agent.execute")
    # A foreign tenant's agent.execute job => the correlation fails tenant-
    # scoped, so it is not counted.

    await _make_integration_job(db, foreign.id, tracked_run.run_id, job_type="agent.execute")

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    safety = (await _get_summary(client, subject, org)).json()["safety"]
    assert safety["public_chat_integration_jobs"] == 1


# ----------------------------------------------------------------------
# Response shape and read-only guarantees
# ----------------------------------------------------------------------


def _assert_no_sensitive_keys(obj: dict, path: str = "") -> None:
    # "theme_token" is a widget theme identifier (e.g. "midnight"), not a
    # credential; every other sensitive-ish name is refused.
    forbidden = (
        "session_token",
        "public_widget_key",
        "widget_key",
        "api_key",
        "authorization",
        "secret",
        "password",
        "email",
    )
    for key, value in obj.items():
        lowered = key.lower()
        if lowered != "theme_token":
            assert not any(
                s in lowered for s in forbidden
            ), f"sensitive key at {path}{key}"
        if isinstance(value, dict):
            _assert_no_sensitive_keys(value, f"{path}{key}.")


@pytest.mark.asyncio
async def test_summary_response_has_no_sensitive_fields(db, org_scope, client):
    org = await org_scope()
    bundle = await _make_widget_session(db, org)
    await _make_message(db, bundle["conversation"])

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    data = (await _get_summary(client, subject, org)).json()
    assert set(data.keys()) == {
        "tenant_id",
        "window",
        "generated_at",
        "config",
        "queue",
        "queue_health",
        "window_summary",
        "rag",
        "safety",
    }
    _assert_no_sensitive_keys(data)


@pytest.mark.asyncio
async def test_summary_is_read_only(db, org_scope, client):
    org = await org_scope()
    bundle = await _make_widget_session(db, org)
    await _make_grounded_event(
        db, await _make_run(db, bundle["ticket"], org.id)
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)

    async with AsyncSessionLocal() as snapshot:
        sessions_before = (
            await snapshot.execute(
                select(func.count(PublicChatSession.id)).where(
                    PublicChatSession.organization_id == org.id
                )
            )
        ).scalar_one()
        events_before = (
            await snapshot.execute(
                select(func.count(AgentActionEvent.id))
                .join(AgentRun, AgentRun.id == AgentActionEvent.agent_run_id)
                .where(AgentRun.organization_id == org.id)
            )
        ).scalar_one()

    first = await _get_summary(client, subject, org)
    second = await _get_summary(client, subject, org)
    assert first.status_code == 200
    first_body = first.json()
    second_body = second.json()
    first_body.pop("generated_at")
    second_body.pop("generated_at")
    assert second_body == first_body

    async with AsyncSessionLocal() as snapshot:
        sessions_after = (
            await snapshot.execute(
                select(func.count(PublicChatSession.id)).where(
                    PublicChatSession.organization_id == org.id
                )
            )
        ).scalar_one()
        events_after = (
            await snapshot.execute(
                select(func.count(AgentActionEvent.id))
                .join(AgentRun, AgentRun.id == AgentActionEvent.agent_run_id)
                .where(AgentRun.organization_id == org.id)
            )
        ).scalar_one()
    assert sessions_after == sessions_before
    assert events_after == events_before