"""Phase 1P.7.3A — expired public-chat session finalizer.

Covers the candidate contract, the expiry lifecycle, race safety, closed_at
semantics, observability, and the CLI of
``scripts/finalize_expired_public_chat_sessions.py`` and
``app/services/public_chat_expiry_service.py`` over the real DB seams:

- an expired live session (``ai_active``/``human_requested``/``human_assigned``)
  is a candidate; a non-expired live session and an already-closed session are
  not;
- a tenant can neither inspect nor finalize another tenant's sessions;
- dry run performs zero writes and its report contains bounded identifiers only;
- apply locks each candidate with ``SELECT ... FOR UPDATE`` (and
  ``populate_existing``), re-checks live status + expiry under the lock, and
  skips (releasing the lock) a candidate whose status changed or whose expiry
  was extended;
- a finalized session is ``closed`` with ``closed_at == original expires_at``;
  the ticket is ``solved`` with ``resolved_at`` recorded; the conversation is
  ``closed``; the assignment trail, customer linkage, ids, and messages are
  preserved; no IntegrationJob or AgentRun is created;
- a real expiry transition emits exactly one close metric and one close log with
  ``closed_by="expiry"``, and a second apply is a no-op;
- reports never contain a token/hash, widget key, message body, email, secret,
  or DATABASE_URL.

No LLM or knowledge-search is exercised: fixtures insert deterministic rows
directly through the ORM. This stage does NOT wire the worker.
"""

import asyncio
import importlib.util
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from prometheus_client import generate_latest
from sqlalchemy import delete, event, func, select, update

os.environ["ENVIRONMENT"] = "development"

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.metrics import PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL
from app.models.agent_run import AgentRun
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.models.ticket import Ticket
from app.repositories.public_chat_repository import PublicChatRepository
from app.services import public_chat_resolution as resolution_module
from app.services.public_chat_expiry_service import (
    EXPIRY_CLOSED_BY,
    ExpiredSessionCandidate,
    ExpirySnapshot,
    PublicChatExpiryService,
)
from app.services.public_chat_service import hash_digest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "finalize_expired_public_chat_sessions.py"
)


def _load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "finalize_expired_public_chat_sessions_tool",
        _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the script's ``from __future__ import
    # annotations`` dataclasses can resolve their own module namespace.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool_module()

EXPIRY_ORG_PREFIX = "pc-expiry-test-"
ALLOWED_ORIGIN = "https://widget.example.test"

# Child-before-parent order so teardown never trips a composite FK.
_PURGE_TABLES = [
    "ticket_events",
    "agent_action_events",
    "knowledge_chunks",
    "ai_request_logs",
    "integration_jobs",
    "agent_runs",
    "knowledge_documents",
    "conversation_messages",
    "public_chat_sessions",
    "public_chat_configurations",
    "conversations",
    "tickets",
    "customers",
    "customer_identities",
    "automation_rules",
    "organization_memberships",
]


# ----------------------------------------------------------------------
# Namespace isolation
# ----------------------------------------------------------------------


async def _purge_org_rows(session, org_ids, *, delete_orgs: bool = False) -> None:
    if not org_ids:
        return
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


async def _purge_namespace() -> list[int]:
    async with AsyncSessionLocal() as session:
        org_ids = [
            org_id
            for (org_id,) in await session.execute(
                select(Organization.id).where(
                    Organization.name.like(f"{EXPIRY_ORG_PREFIX}%")
                )
            )
        ]
        await _purge_org_rows(session, org_ids, delete_orgs=True)
        return org_ids


async def _assert_namespace_clean() -> None:
    async with AsyncSessionLocal() as session:
        leftover = await session.execute(
            select(Organization.id).where(
                Organization.name.like(f"{EXPIRY_ORG_PREFIX}%")
            )
        )
        assert not leftover.all(), "public-chat expiry namespace leaked organizations"


@pytest.fixture(scope="module", autouse=True)
def _namespace_isolated():
    asyncio.run(_purge_namespace())
    yield
    asyncio.run(_assert_namespace_clean())


@pytest_asyncio.fixture
async def org_scope(db):
    org_ids: list[int] = []

    async def make(external_id: str | None = None) -> Organization:
        suffix = uuid.uuid4().hex[:10]
        org = Organization(
            name=f"{EXPIRY_ORG_PREFIX}{suffix}",
            external_id=external_id or f"pc-expiry-{suffix}",
        )
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        await db.commit()
        return org

    yield make

    await _purge_org_rows(db, org_ids)
    await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


# ----------------------------------------------------------------------
# Row builders
# ----------------------------------------------------------------------


async def _get_or_create_config(
    db,
    org: Organization,
    *,
    widget_key_hash: str | None = None,
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
        public_widget_key_hash=widget_key_hash
        or hash_digest(f"pk_live_{secrets.token_hex(20)}"),
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


async def _make_session(
    db,
    org: Organization,
    *,
    session_status: str = "ai_active",
    ticket_status: str = "new",
    conversation_status: str = "open",
    resolved_at: datetime | None = None,
    expires_at: datetime | None = None,
    assigned_to_subject: str | None = None,
    assigned_at: datetime | None = None,
    released_at: datetime | None = None,
    token_hash: str | None = None,
) -> dict:
    """Insert a deterministic session + linked conversation/ticket for the org.

    ``expires_at`` defaults to one hour in the past, so the created session is
    expired (the normal shape under test). Mirrors the production shape observed
    in RISPU: session live, ticket ``new``, conversation ``open``.
    """
    config = await _get_or_create_config(db, org)

    if expires_at is None:
        expires_at = datetime.now(UTC) - timedelta(hours=1)

    conversation = Conversation(
        organization_id=org.id,
        provider="public_chat",
        channel="public_chat",
        status=conversation_status,
    )
    db.add(conversation)
    await db.flush()

    ticket = Ticket(
        organization_id=org.id,
        subject="Widget inquiry",
        description=".",
        status=ticket_status,
        priority="medium",
        source="public-chat-widget",
        resolved_at=resolved_at,
    )
    db.add(ticket)
    await db.flush()

    closed_at = expires_at if session_status == "closed" else None

    session = PublicChatSession(
        organization_id=org.id,
        configuration_id=config.id,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        token_hash=token_hash or hash_digest(secrets.token_hex(32)),
        status=session_status,
        expires_at=expires_at,
        closed_at=closed_at,
        assigned_to_subject=assigned_to_subject,
        assigned_at=assigned_at,
        released_at=released_at,
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


async def _make_message(db, conversation: Conversation, *, body: str) -> None:
    db.add(
        ConversationMessage(
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            provider="public_chat",
            direction="inbound",
            visibility="visible",
            body=body,
        )
    )
    await db.commit()


async def _fresh_row(db, model, row_id: int):
    result = await db.execute(
        select(model)
        .where(model.id == row_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


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


def _closed_total_for(closed_by: str) -> int:
    prefix = f'cxops_public_chat_sessions_closed_total{{closed_by="{closed_by}"}}'
    text = generate_latest(PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL).decode()
    total = 0.0
    for line in text.splitlines():
        if line.startswith(prefix):
            total += float(line.rsplit(" ", 1)[-1])
    return int(total)


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))


async def _collect(db, org: Organization) -> ExpirySnapshot:
    return await PublicChatExpiryService.collect_expired_sessions(
        db, organization_id=org.id
    )


def _stale_snapshot_factory(
    *,
    session_id: int,
    ticket_id: int,
    conversation_id: int,
    session_status: str,
    expires_at: datetime,
):
    """A fabricated snapshot that always reports one stale candidate."""

    async def fake(db_, *, organization_id, now=None):
        return ExpirySnapshot(
            organization_id=organization_id,
            expired_sessions_checked=1,
            candidates=[
                ExpiredSessionCandidate(
                    session_id=session_id,
                    ticket_id=ticket_id,
                    conversation_id=conversation_id,
                    session_status=session_status,
                    ticket_status="new",
                    conversation_status="open",
                    expires_at=expires_at,
                )
            ],
        )

    return fake


# ----------------------------------------------------------------------
# A-E: candidate definition
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_status",
    ["ai_active", "human_requested", "human_assigned"],
)
async def test_expired_live_status_is_a_candidate(db, org_scope, session_status):
    org = await org_scope()
    case = await _make_session(db, org, session_status=session_status)

    snapshot = await _collect(db, org)

    assert snapshot.expired_sessions_checked == 1
    assert [c.session_id for c in snapshot.candidates] == [case["session"].id]
    candidate = snapshot.candidates[0]
    assert candidate.session_status == session_status
    assert candidate.ticket_id == case["ticket"].id
    assert candidate.conversation_id == case["conversation"].id
    assert candidate.ticket_status == "new"
    assert candidate.conversation_status == "open"


@pytest.mark.asyncio
async def test_non_expired_live_session_is_not_a_candidate(db, org_scope):
    org = await org_scope()
    await _make_session(
        db,
        org,
        session_status="ai_active",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )

    snapshot = await _collect(db, org)

    assert snapshot.expired_sessions_checked == 0
    assert snapshot.candidates == []


@pytest.mark.asyncio
async def test_already_closed_expired_session_is_not_a_candidate(db, org_scope):
    org = await org_scope()
    await _make_session(db, org, session_status="closed")

    snapshot = await _collect(db, org)

    assert snapshot.expired_sessions_checked == 0
    assert snapshot.candidates == []


# ----------------------------------------------------------------------
# F: tenant isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_cannot_inspect_or_finalize_another_tenant(db, org_scope):
    org_a = await org_scope()
    org_b = await org_scope()
    org_a_id = org_a.id
    case_b = await _make_session(db, org_b, session_status="ai_active")
    session_b_id = case_b["session"].id
    ticket_b_id = case_b["ticket"].id

    # Tenant A's scan sees nothing of tenant B.
    snapshot_a = await _collect(db, org_a)
    assert snapshot_a.candidates == []

    # A lock read scoped to tenant A cannot reach tenant B's session.
    locked = await PublicChatRepository.get_session_for_update_for_tenant(
        db, session_b_id, org_a_id
    )
    assert locked is None
    await db.rollback()

    # Finalizing tenant A never touches tenant B. ``org_a_id`` was captured as a
    # plain int above because the rollback expires every ORM object in ``db``.
    result = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org_a_id
    )
    assert result.finalized == 0

    session_b = await _fresh_row(db, PublicChatSession, session_b_id)
    ticket_b = await _fresh_row(db, Ticket, ticket_b_id)
    assert session_b.status == "ai_active"
    assert ticket_b.status == "new"


# ----------------------------------------------------------------------
# G: dry run has no writes
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_performs_zero_writes(db, org_scope):
    org = await org_scope()
    case = await _make_session(db, org, session_status="ai_active")
    session_id = case["session"].id
    ticket_id = case["ticket"].id
    conversation_id = case["conversation"].id

    snapshot = await _collect(db, org)
    report = tool.render_dry_run(tenant_external_id=org.external_id, snapshot=snapshot)

    assert tool.DRY_RUN_FOOTER in report
    assert str(session_id) in report
    assert str(org.id) in report

    session = await _fresh_row(db, PublicChatSession, session_id)
    ticket = await _fresh_row(db, Ticket, ticket_id)
    conversation = await _fresh_row(db, Conversation, conversation_id)
    assert session.status == "ai_active"
    assert session.closed_at is None
    assert ticket.status == "new"
    assert ticket.resolved_at is None
    assert conversation.status == "open"


# ----------------------------------------------------------------------
# H-I: locked read
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_uses_for_update_locked_reread(db, org_scope):
    org = await org_scope()
    await _make_session(db, org, session_status="ai_active")

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        result = await PublicChatExpiryService.finalize_expired_sessions(
            db, organization_id=org.id
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert result.finalized == 1

    locked = [
        statement
        for statement in statements
        if "FOR UPDATE" in statement.upper() and "public_chat_sessions" in statement
    ]
    assert locked, "finalize must lock the session row with SELECT ... FOR UPDATE"
    assert all("organization_id" in statement for statement in locked)
    assert any("public_chat_sessions.id" in statement for statement in locked)


@pytest.mark.asyncio
async def test_locked_reread_refreshes_stale_identity_map(db, org_scope):
    org = await org_scope()
    case = await _make_session(db, org, session_status="ai_active")
    session_id = case["session"].id

    primed = (
        await db.execute(
            select(PublicChatSession).where(PublicChatSession.id == session_id)
        )
    ).scalar_one()
    assert primed.status == "ai_active"

    async with AsyncSessionLocal() as other:
        await other.execute(
            update(PublicChatSession)
            .where(PublicChatSession.id == session_id)
            .values(status="closed", closed_at=datetime.now(UTC))
        )
        await other.commit()

    reread = await PublicChatRepository.get_session_for_update_for_tenant(
        db, session_id, org.id
    )
    try:
        assert reread is not None
        # populate_existing overwrote the stale live copy with the committed row.
        assert reread.status == "closed"
        assert reread is primed
    finally:
        await db.rollback()


# ----------------------------------------------------------------------
# J-K: race safety
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_changed_after_snapshot_is_rolled_back_and_skipped(
    db, org_scope, monkeypatch
):
    org = await org_scope()
    case = await _make_session(db, org, session_status="closed")
    session_id = case["session"].id
    ticket_id = case["ticket"].id
    conversation_id = case["conversation"].id
    expires_at = case["session"].expires_at

    monkeypatch.setattr(
        PublicChatExpiryService,
        "collect_expired_sessions",
        _stale_snapshot_factory(
            session_id=session_id,
            ticket_id=ticket_id,
            conversation_id=conversation_id,
            session_status="ai_active",
            expires_at=expires_at,
        ),
    )

    result = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    assert result.finalized == 0
    assert result.skipped == [session_id]
    # Lock released by the rollback (the fake snapshot does no I/O afterwards).
    assert db.in_transaction() is False

    ticket = await _fresh_row(db, Ticket, ticket_id)
    conversation = await _fresh_row(db, Conversation, conversation_id)
    session = await _fresh_row(db, PublicChatSession, session_id)
    assert session.status == "closed"
    assert ticket.status == "new"
    assert conversation.status == "open"


@pytest.mark.asyncio
async def test_expiry_extended_after_snapshot_is_rolled_back_and_skipped(
    db, org_scope, monkeypatch
):
    org = await org_scope()
    case = await _make_session(db, org, session_status="ai_active")
    session_id = case["session"].id
    ticket_id = case["ticket"].id
    conversation_id = case["conversation"].id
    expires_at = case["session"].expires_at

    monkeypatch.setattr(
        PublicChatExpiryService,
        "collect_expired_sessions",
        _stale_snapshot_factory(
            session_id=session_id,
            ticket_id=ticket_id,
            conversation_id=conversation_id,
            session_status="ai_active",
            expires_at=expires_at,
        ),
    )

    # A concurrent writer extends the expiry into the future.
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(PublicChatSession)
            .where(PublicChatSession.id == session_id)
            .values(expires_at=datetime.now(UTC) + timedelta(hours=24))
        )
        await other.commit()

    result = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    assert result.finalized == 0
    assert result.skipped == [session_id]
    assert db.in_transaction() is False

    session = await _fresh_row(db, PublicChatSession, session_id)
    ticket = await _fresh_row(db, Ticket, ticket_id)
    conversation = await _fresh_row(db, Conversation, conversation_id)
    assert session.status == "ai_active"
    assert session.closed_at is None
    assert ticket.status == "new"
    assert conversation.status == "open"


# ----------------------------------------------------------------------
# L-M-N: successful lifecycle + preservation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_expiry_finalizes_through_shared_lifecycle(db, org_scope):
    org = await org_scope()
    case = await _make_session(db, org, session_status="ai_active")
    session_id = case["session"].id
    ticket_id = case["ticket"].id
    conversation_id = case["conversation"].id
    original_expires_at = case["session"].expires_at

    result = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    assert result.finalized == 1
    assert result.candidates_after == 0

    session = await _fresh_row(db, PublicChatSession, session_id)
    ticket = await _fresh_row(db, Ticket, ticket_id)
    conversation = await _fresh_row(db, Conversation, conversation_id)

    assert session.status == "closed"
    assert session.closed_at == original_expires_at
    assert ticket.status == "solved"
    assert ticket.resolved_at is not None
    assert conversation.status == "closed"


@pytest.mark.asyncio
async def test_expiry_preserves_assignment_trail(db, org_scope):
    org = await org_scope()
    assigned_at = datetime.now(UTC) - timedelta(hours=2)
    released_at = datetime.now(UTC) - timedelta(hours=1)
    case = await _make_session(
        db,
        org,
        session_status="human_assigned",
        assigned_to_subject="staff-subject-1",
        assigned_at=assigned_at,
        released_at=released_at,
    )
    session_id = case["session"].id

    await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    session = await _fresh_row(db, PublicChatSession, session_id)
    assert session.status == "closed"
    assert session.assigned_to_subject == "staff-subject-1"
    assert session.assigned_at == assigned_at
    assert session.released_at == released_at


@pytest.mark.asyncio
async def test_expiry_preserves_messages(db, org_scope):
    org = await org_scope()
    case = await _make_session(db, org, session_status="ai_active")
    await _make_message(db, case["conversation"], body="Please help me.")

    await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    assert await _count(db, ConversationMessage, org.id) == 1
    message = (
        await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == case["conversation"].id
            )
        )
    ).scalar_one()
    assert message.body == "Please help me."


# ----------------------------------------------------------------------
# O-P: no side effects
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_creates_no_integration_job_or_agent_run(db, org_scope):
    org = await org_scope()
    await _make_session(db, org, session_status="ai_active")

    assert await _count(db, IntegrationJob, org.id) == 0
    assert await _count(db, AgentRun, org.id) == 0

    await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    assert await _count(db, IntegrationJob, org.id) == 0
    assert await _count(db, AgentRun, org.id) == 0


# ----------------------------------------------------------------------
# Q-R: observability
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_emits_close_metric_exactly_once(db, org_scope):
    org = await org_scope()
    await _make_session(db, org, session_status="ai_active")

    before = _closed_total_for("expiry")
    first = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )
    after_first = _closed_total_for("expiry")
    second = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )
    after_second = _closed_total_for("expiry")

    assert first.finalized == 1
    assert after_first == before + 1
    assert second.finalized == 0
    assert after_second == after_first


@pytest.mark.asyncio
async def test_expiry_emits_close_log_exactly_once(db, org_scope, monkeypatch):
    org = await org_scope()
    await _make_session(db, org, session_status="ai_active")

    recorder = _RecordingLogger()
    monkeypatch.setattr(resolution_module, "log", recorder)

    await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )
    await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    close_events = [
        kwargs
        for event_name, kwargs in recorder.events
        if event_name == "public_chat_session_closed"
    ]
    assert len(close_events) == 1
    assert close_events[0]["closed_by"] == "expiry"


# ----------------------------------------------------------------------
# S: idempotency
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_apply_is_idempotent(db, org_scope):
    org = await org_scope()
    await _make_session(db, org, session_status="ai_active")

    first = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )
    second = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )

    assert first.candidates_before == 1
    assert first.finalized == 1
    assert first.candidates_after == 0
    assert second.candidates_before == 0
    assert second.finalized == 0
    assert second.candidates_after == 0


# ----------------------------------------------------------------------
# T: report safety
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_contains_no_sensitive_values(db, org_scope):
    org = await org_scope()
    suffix = uuid.uuid4().hex[:8]
    token_marker = f"TOKENHASH-{suffix}-" + "a" * 30
    widget_marker = f"WIDGETKEYHASH-{suffix}-" + "b" * 24
    body_marker = f"MESSAGE-BODY-{suffix}"
    email_marker = f"secret-{suffix}@example.com"

    case = await _make_session(db, org, session_status="ai_active", token_hash=token_marker)
    case["config"].public_widget_key_hash = widget_marker
    await db.flush()
    case["ticket"].requester_email = email_marker
    await db.flush()
    await _make_message(db, case["conversation"], body=body_marker)
    await db.commit()

    snapshot = await _collect(db, org)
    report = tool.render_dry_run(tenant_external_id=org.external_id, snapshot=snapshot)
    result = await PublicChatExpiryService.finalize_expired_sessions(
        db, organization_id=org.id
    )
    apply_report = tool.render_apply_result(
        tenant_external_id=org.external_id, result=result
    )

    combined = report + "\n" + apply_report
    for secret_value in (
        token_marker,
        widget_marker,
        body_marker,
        email_marker,
        settings.database_url,
    ):
        assert secret_value not in combined


# ----------------------------------------------------------------------
# U-V: CLI
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tenant_resolves_to_none(db):
    assert (
        await tool.resolve_organization_id(db, f"does-not-exist-{uuid.uuid4().hex}")
        is None
    )


@pytest.mark.asyncio
async def test_unknown_tenant_exits_with_code_2():
    args = tool.parse_args(["--tenant", f"does-not-exist-{uuid.uuid4().hex}"])
    assert await tool._main(args) == 2


def test_parse_args_defaults_to_dry_run():
    args = tool.parse_args(["--tenant", "rispu"])
    assert args.tenant == "rispu"
    assert args.apply is False


def test_parse_args_apply_mode():
    args = tool.parse_args(["--tenant", "rispu", "--apply"])
    assert args.apply is True


def test_parse_args_requires_tenant():
    with pytest.raises(SystemExit):
        tool.parse_args([])


def test_parse_args_rejects_empty_tenant():
    with pytest.raises(SystemExit):
        tool.parse_args(["--tenant", "   "])


def test_parse_args_rejects_apply_and_dry_run_together():
    with pytest.raises(SystemExit):
        tool.parse_args(["--tenant", "rispu", "--apply", "--dry-run"])


# ----------------------------------------------------------------------
# Bounded actor contract
# ----------------------------------------------------------------------


def test_expiry_actor_is_in_bounded_close_actor_set():
    from app.services.public_chat_resolution import PUBLIC_CHAT_CLOSE_ACTORS

    assert PUBLIC_CHAT_CLOSE_ACTORS == {"customer", "staff", "expiry"}
    assert EXPIRY_CLOSED_BY in PUBLIC_CHAT_CLOSE_ACTORS


@pytest.mark.asyncio
async def test_close_lifecycle_rejects_unbounded_actor():
    # The guard raises before any DB work, so no session/DB is required.
    with pytest.raises(ValueError):
        await resolution_module.PublicChatResolutionService.close_lifecycle(
            None,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
            organization_id=1,
            when=datetime.now(UTC),
            closed_by="tenant-42",  # type: ignore[arg-type]
        )
