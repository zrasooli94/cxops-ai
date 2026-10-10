"""Phase 1P.7 — staff public-chat resolve endpoint.

This suite proves the security-critical properties of the staff resolve
operation over the real HTTP route and the real service/DB seams:

- the resolve endpoint is capability-gated on TICKET_WRITE (a read-only member
  can list the handoff queue but cannot resolve)
- resolving a live session closes the session, solves the linked ticket, and
  closes the linked conversation in one tenant-scoped transition
- SLA resolution semantics are preserved: the ticket's ``resolved_at``
  milestone is recorded through the same hook the ticket update lifecycle uses
- live states (``ai_active``, ``human_requested``, ``human_assigned``) and
  expired-but-unclosed sessions are all resolvable
- the assignment trail (``assigned_to_subject`` / ``assigned_at``) is preserved
  on resolve for audit and never cleared
- resolve is idempotent: a retry on an already-closed session succeeds without
  re-emitting the close metric/log, repairs a missing SLA milestone when the
  first attempt failed after the atomic commit, and reconciles the documented
  Phase 1P.7.1 legacy drift (closed session whose linked ticket/conversation are
  still open) without rewriting ``closed_at`` or emitting a close signal
- resolve never leaks across tenants (a foreign org's session is 404 and its
  rows stay untouched)
- nothing is scheduled or deleted: no IntegrationJob or AgentRun rows are
  created and conversation messages are preserved

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
from prometheus_client import generate_latest
from sqlalchemy import delete, func, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-p1p7r-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "cxops-unit-test"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.metrics import PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL
from app.main import app
from app.models.agent_run import AgentRun
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
    hash_digest,
)
from app.services.ticket_sla_service import TicketSLAService

ALLOWED_ORIGIN = "https://widget.example.test"
TENANT_HEADER = "x-cxops-organization-id"

P1P7R_ORG_PREFIX = "p1p7r-test-"

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
        "AUTH_JWT_ISSUER": "test-p1p7r-issuer",
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
def _p1p7r_namespace_isolated():
    asyncio.run(_purge_p1p7r_namespace())
    yield
    asyncio.run(_assert_p1p7r_namespace_clean())


async def _purge_p1p7r_namespace() -> list[int]:
    async with AsyncSessionLocal() as session:
        org_ids = [
            org_id
            for (org_id,) in await session.execute(
                select(Organization.id).where(
                    Organization.name.like(f"{P1P7R_ORG_PREFIX}%")
                )
            )
        ]
        await _purge_org_tables(session, org_ids, delete_orgs=True)
        return org_ids


async def _assert_p1p7r_namespace_clean() -> None:
    async with AsyncSessionLocal() as session:
        leftover = await session.execute(
            select(Organization.id).where(
                Organization.name.like(f"{P1P7R_ORG_PREFIX}%")
            )
        )
        assert not leftover.all(), "Phase 1P.7R namespace leaked organizations"


async def _purge_org_tables(
    session,
    org_ids: list[int],
    *,
    delete_orgs: bool = False,
) -> None:
    if not org_ids:
        return
    from app.models.agent_action_event import AgentActionEvent
    from app.models.base import Base

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
        org = Organization(name=f"{P1P7R_ORG_PREFIX}{uuid.uuid4().hex[:10]}")
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
    expires_at: datetime | None = None,
    closed_at: datetime | None = None,
    assigned_to_subject: str | None = None,
    assigned_at: datetime | None = None,
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
        assigned_to_subject=assigned_to_subject,
        assigned_at=assigned_at,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "config": config,
        "conversation": conversation,
        "ticket": ticket,
        "session": session,
    }


async def _make_message(db, conversation: Conversation) -> None:
    db.add(
        ConversationMessage(
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            provider="public_chat",
            direction="inbound",
            visibility="visible",
            body="I need help with my order.",
        )
    )
    await db.commit()


async def _count(db, model, org_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(model)
                .where(model.organization_id == org_id)
            )
        ).scalar_one()
    )


async def _fresh_row(db, model, row_id: int):
    """Re-read a committed row, overwriting any identity-mapped stale copy."""
    result = await db.execute(
        select(model)
        .where(model.id == row_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _resolve(client, headers, session_id: int):
    return await client.post(
        f"/staff/public-chat/sessions/{session_id}/resolve",
        headers=headers,
    )


async def _resolve_or_raise(client, headers, session_id: int):
    """POST resolve, tolerating a debug-mode re-raise from the ASGI app.

    In development the app runs with ``debug=True``, so an unhandled exception
    inside a route propagates out of Starlette and is re-raised by the test
    client instead of serializing as a 500. ``None`` means the request did not
    produce an HTTP response (the server raised).
    """
    try:
        return await _resolve(client, headers, session_id)
    except Exception:  # noqa: BLE001 -- ASGI debug re-raise boundary
        return None


def _closed_total_for(closed_by: str) -> int:
    """Current value of the close counter for one bounded actor label."""
    total = 0.0
    prefix = f'cxops_public_chat_sessions_closed_total{{closed_by="{closed_by}"}}'
    text = generate_latest(PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL).decode()
    for line in text.splitlines():
        if line.startswith(prefix):
            total += float(line.rsplit(" ", 1)[-1])
    return int(total)


# ----------------------------------------------------------------------
# Resolve authorization
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_requires_ticket_write(db, client, org_scope):
    org = await org_scope()
    bundle = await _make_widget_session(db, org, status="human_requested")

    read_only_subject = f"viewer-{uuid.uuid4().hex[:8]}"
    await _add_membership(
        db,
        read_only_subject,
        org.id,
        role=OrganizationRole.VIEWER,
    )
    read_headers = _staff_headers(read_only_subject, org.id)

    listing = await client.get("/staff/public-chat/handoff", headers=read_headers)
    assert listing.status_code == 200, listing.text

    rejected = await client.post(
        f"/staff/public-chat/sessions/{bundle['session'].id}/resolve",
        headers=read_headers,
    )
    assert rejected.status_code == 403, rejected.text

    owner_subject = f"owner-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, owner_subject, org.id)
    owner_headers = _staff_headers(owner_subject, org.id)
    accepted = await client.post(
        f"/staff/public-chat/sessions/{bundle['session'].id}/resolve",
        headers=owner_headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "closed"


# ----------------------------------------------------------------------
# Resolve lifecycle and side effects
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_human_requested_closes_session_ticket_and_conversation(
    db,
    client,
    org_scope,
):
    org = await org_scope()
    bundle = await _make_widget_session(db, org, status="human_requested")
    await _make_message(db, bundle["conversation"])
    conversation_id = bundle["conversation"].id
    assert await _count(db, ConversationMessage, org.id) == 1

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    response = await _resolve(client, headers, bundle["session"].id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "closed"
    assert data["session_id"] == bundle["session"].id
    assert data["conversation_id"] == conversation_id
    assert data["ticket_id"] == bundle["ticket"].id

    session = await _fresh_row(db, PublicChatSession, bundle["session"].id)
    assert session.status == "closed"
    assert session.closed_at is not None
    assert session.assigned_to_subject is None

    ticket = await _fresh_row(db, Ticket, bundle["ticket"].id)
    assert ticket.status == "solved"
    assert ticket.resolved_at is not None

    conversation = await _fresh_row(db, Conversation, conversation_id)
    assert conversation.status == "closed"

    assert await _count(db, ConversationMessage, org.id) == 1


@pytest.mark.asyncio
async def test_resolve_human_assigned_preserves_assignment_trail(
    db,
    client,
    org_scope,
):
    org = await org_scope()
    assignee = f"assignee-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    bundle = await _make_widget_session(
        db,
        org,
        status="human_assigned",
        assigned_to_subject=assignee,
        assigned_at=now,
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    response = await _resolve(client, headers, bundle["session"].id)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"

    session = await _fresh_row(db, PublicChatSession, bundle["session"].id)
    assert session.status == "closed"
    assert session.assigned_to_subject == assignee
    assert session.assigned_at is not None
    assert session.released_at is None


@pytest.mark.asyncio
async def test_resolve_ai_active_session_closes_ticket_and_conversation(
    db,
    client,
    org_scope,
):
    org = await org_scope()
    bundle = await _make_widget_session(db, org, status="ai_active")

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    response = await _resolve(client, headers, bundle["session"].id)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"

    ticket = await _fresh_row(db, Ticket, bundle["ticket"].id)
    assert ticket.status == "solved"
    conversation = await _fresh_row(db, Conversation, bundle["conversation"].id)
    assert conversation.status == "closed"


@pytest.mark.asyncio
async def test_resolve_expired_session_is_still_resolvable(db, client, org_scope):
    org = await org_scope()
    bundle = await _make_widget_session(
        db,
        org,
        status="human_requested",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    response = await _resolve(client, headers, bundle["session"].id)
    assert response.status_code == 200, response.text
    session = await _fresh_row(db, PublicChatSession, bundle["session"].id)
    assert session.status == "closed"


@pytest.mark.asyncio
async def test_resolve_is_idempotent(db, client, org_scope):
    org = await org_scope()
    bundle = await _make_widget_session(db, org, status="human_requested")
    await _make_message(db, bundle["conversation"])

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    first = await _resolve(client, headers, bundle["session"].id)
    assert first.status_code == 200, first.text

    ticket_after_first = await _fresh_row(db, Ticket, bundle["ticket"].id)
    resolved_at_first = ticket_after_first.resolved_at

    second = await _resolve(client, headers, bundle["session"].id)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "closed"
    assert second.json()["session_id"] == first.json()["session_id"]

    session = await _fresh_row(db, PublicChatSession, bundle["session"].id)
    assert session.status == "closed"
    ticket = await _fresh_row(db, Ticket, bundle["ticket"].id)
    assert ticket.status == "solved"
    assert ticket.resolved_at == resolved_at_first
    conversation = await _fresh_row(db, Conversation, bundle["conversation"].id)
    assert conversation.status == "closed"
    assert await _count(db, ConversationMessage, org.id) == 1


@pytest.mark.asyncio
async def test_resolve_repairs_legacy_closed_session_drift_without_new_close_signal(
    db,
    client,
    org_scope,
):
    """Staff Resolve reconciles the documented Phase 1P.7.1 legacy drift.

    A session closed before Phase 1P.7.1 can have ``status = 'closed'`` while
    its linked ticket is still open and its linked conversation is still open.
    Resolve is an idempotent reconciliation for that state -- not a new close
    transition: it solves the ticket, closes the conversation, and records
    ``resolved_at`` through the application, with no close metric, no rewrite of
    ``closed_at``, and no side effects on history or jobs/runs.
    """
    org = await org_scope()
    original_closed_at = datetime.now(UTC) - timedelta(hours=5)
    bundle = await _make_widget_session(
        db,
        org,
        status="closed",
        closed_at=original_closed_at,
    )
    await _make_message(db, bundle["conversation"])
    session_id = bundle["session"].id
    ticket_id = bundle["ticket"].id
    conversation_id = bundle["conversation"].id

    # Documented legacy drift is present: closed session, open ticket + conv.
    ticket = await _fresh_row(db, Ticket, ticket_id)
    assert ticket.status == "open"
    conversation = await _fresh_row(db, Conversation, conversation_id)
    assert conversation.status == "open"

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    before = _closed_total_for("staff")

    response = await _resolve(client, headers, session_id)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"

    session = await _fresh_row(db, PublicChatSession, session_id)
    assert session.status == "closed"
    # closed_at is preserved: a repair is not a (re)close.
    assert session.closed_at == original_closed_at

    ticket = await _fresh_row(db, Ticket, ticket_id)
    assert ticket.status == "solved"
    assert ticket.resolved_at is not None

    conversation = await _fresh_row(db, Conversation, conversation_id)
    assert conversation.status == "closed"

    # A legacy repair is NOT a close transition: no close metric.
    assert _closed_total_for("staff") == before
    assert await _count(db, ConversationMessage, org.id) == 1
    assert await _count(db, IntegrationJob, org.id) == 0
    assert await _count(db, AgentRun, org.id) == 0


@pytest.mark.asyncio
async def test_resolve_retry_repairs_missing_sla_milestone_without_duplicate_metric(
    db,
    client,
    org_scope,
    monkeypatch,
):
    """A failed SLA follow-up is audited once and a retry repairs resolved_at.

    The atomic core commit (session + ticket + conversation) succeeds, then the
    close audit signal (metric) is emitted, then the SLA follow-up fails. The
    first request therefore errors, but the close is already audited exactly
    once. The retried resolve sees an already-closed session whose linked ticket
    still has ``resolved_at IS NULL`` and records the missing milestone --
    without emitting a second close metric or log and without touching the
    preserved history or ``closed_at``.
    """
    org = await org_scope()
    bundle = await _make_widget_session(db, org, status="human_requested")
    await _make_message(db, bundle["conversation"])

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    before = _closed_total_for("staff")

    async def _fail_resolution(*_args, **_kwargs):
        raise RuntimeError("SLA follow-up failed after core commit")

    with monkeypatch.context() as mp:
        mp.setattr(TicketSLAService, "record_resolution", _fail_resolution)
        first = await _resolve_or_raise(client, headers, bundle["session"].id)
    # The request errors (500 or a debug re-raise) once SLA recording fails.
    assert first is None or first.status_code >= 500

    session = await _fresh_row(db, PublicChatSession, bundle["session"].id)
    assert session.status == "closed"
    assert session.closed_at is not None
    ticket = await _fresh_row(db, Ticket, bundle["ticket"].id)
    assert ticket.status == "solved"
    assert ticket.resolved_at is None
    # Close audit signal was emitted before the SLA failure: exactly one.
    assert _closed_total_for("staff") == before + 1

    second = await _resolve(client, headers, bundle["session"].id)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "closed"
    assert second.json()["ticket_id"] == bundle["ticket"].id

    session = await _fresh_row(db, PublicChatSession, bundle["session"].id)
    assert session.status == "closed"
    ticket = await _fresh_row(db, Ticket, bundle["ticket"].id)
    assert ticket.status == "solved"
    assert ticket.resolved_at is not None
    conversation = await _fresh_row(db, Conversation, bundle["conversation"].id)
    assert conversation.status == "closed"
    # No second non-closed -> closed transition, so no second metric or log.
    assert _closed_total_for("staff") == before + 1
    # The preserved history and the no-side-effect guarantees still hold.
    assert await _count(db, ConversationMessage, org.id) == 1
    assert await _count(db, IntegrationJob, org.id) == 0
    assert await _count(db, AgentRun, org.id) == 0


@pytest.mark.asyncio
async def test_resolve_does_not_schedule_jobs_or_runs(db, client, org_scope):
    org = await org_scope()
    bundle = await _make_widget_session(db, org, status="human_requested")

    jobs_before = await _count(db, IntegrationJob, org.id)
    runs_before = await _count(db, AgentRun, org.id)

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = _staff_headers(subject, org.id)

    response = await _resolve(client, headers, bundle["session"].id)
    assert response.status_code == 200, response.text

    assert await _count(db, IntegrationJob, org.id) == jobs_before
    assert await _count(db, AgentRun, org.id) == runs_before


# ----------------------------------------------------------------------
# Tenant isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_foreign_tenant_is_404_and_leaves_rows_untouched(
    db,
    client,
    org_scope,
):
    org_a = await org_scope()
    org_b = await org_scope()
    bundle = await _make_widget_session(db, org_a, status="human_requested")
    await _make_message(db, bundle["conversation"])

    subject_b = f"staff-b-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject_b, org_b.id)
    headers_b = _staff_headers(subject_b, org_b.id)

    rogue = await _resolve(client, headers_b, bundle["session"].id)
    assert rogue.status_code == 404, rogue.text

    session = await _fresh_row(db, PublicChatSession, bundle["session"].id)
    assert session.status == "human_requested"
    ticket = await _fresh_row(db, Ticket, bundle["ticket"].id)
    assert ticket.status == "open"
    conversation = await _fresh_row(db, Conversation, bundle["conversation"].id)
    assert conversation.status == "open"

    subject_a = f"staff-a-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject_a, org_a.id)
    headers_a = _staff_headers(subject_a, org_a.id)
    clean = await _resolve(client, headers_a, bundle["session"].id)
    assert clean.status_code == 200, clean.text
    assert await _count(db, ConversationMessage, org_a.id) == 1