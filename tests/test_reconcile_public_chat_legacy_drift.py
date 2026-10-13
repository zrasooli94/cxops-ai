"""Phase 1P.7.2 — legacy public-chat close-drift reconciliation tool.

Covers the audit + repair contract of
``scripts/reconcile_public_chat_legacy_drift.py`` over the real DB seams:

- the dry run reports every documented drift flavor (closed session with an
  open linked ticket, an open linked conversation, or a missing resolution
  milestone) and never mutates anything
- a fully-consistent closed session is not reported
- live sessions (``ai_active``/``human_requested``/``human_assigned``) are
  never candidates and are never touched
- a tenant can neither inspect nor reconcile another tenant's sessions
- apply repairs the drift through the existing shared lifecycle
  (``PublicChatResolutionService.close_lifecycle``), preserving ``closed_at``,
  the conversation history, and never creating an IntegrationJob or AgentRun
- the repair is not a close transition: it emits no close metric or close log
- the tool is idempotent: a second apply finds zero drift and does nothing
- a session whose state changed after the scan is skipped, not mutated
- reports contain bounded identifiers only - never a token/hash, widget key,
  message body, email, secret, or DATABASE_URL

No LLM or knowledge-search is exercised: fixtures insert deterministic rows
directly through the ORM.
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
from app.services import public_chat_resolution as resolution_module
from app.services.public_chat_resolution import PublicChatResolutionService
from app.services.public_chat_service import hash_digest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "reconcile_public_chat_legacy_drift.py"
)


def _load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "reconcile_public_chat_legacy_drift_tool",
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

DRIFT_ORG_PREFIX = "pc-drift-test-"
ALLOWED_ORIGIN = "https://widget.example.test"

# Child-before-parent order so teardown never trips a composite FK.
_PURGE_TABLES = [
    "ticket_events",
    "agent_action_events",
    "knowledge_chunks",
    "ai_usage_reservations",
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
                    Organization.name.like(f"{DRIFT_ORG_PREFIX}%")
                )
            )
        ]
        await _purge_org_rows(session, org_ids, delete_orgs=True)
        return org_ids


async def _assert_namespace_clean() -> None:
    async with AsyncSessionLocal() as session:
        leftover = await session.execute(
            select(Organization.id).where(
                Organization.name.like(f"{DRIFT_ORG_PREFIX}%")
            )
        )
        assert not leftover.all(), "public-chat drift namespace leaked organizations"


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
            name=f"{DRIFT_ORG_PREFIX}{suffix}",
            external_id=external_id or f"pc-drift-{suffix}",
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


_SENTINEL = object()


async def _make_session(
    db,
    org: Organization,
    *,
    session_status: str = "closed",
    ticket_status: str = "open",
    conversation_status: str = "open",
    resolved_at: datetime | None = None,
    closed_at: datetime | None | object = _SENTINEL,
    token_hash: str | None = None,
) -> dict:
    """Insert a deterministic session + linked conversation/ticket for the org."""
    config = await _get_or_create_config(db, org)

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

    if closed_at is _SENTINEL:
        closed_at = (
            datetime.now(UTC) - timedelta(hours=1)
            if session_status == "closed"
            else None
        )

    session = PublicChatSession(
        organization_id=org.id,
        configuration_id=config.id,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        token_hash=token_hash or hash_digest(secrets.token_hex(32)),
        status=session_status,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        closed_at=closed_at,
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


async def _snapshot_for(db, org: Organization) -> "tool.DriftSnapshot":
    return await tool.collect_drift_snapshot(
        db,
        tenant_external_id=org.external_id,
        organization_id=org.id,
    )


# ----------------------------------------------------------------------
# Dry-run reporting
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_reports_closed_session_with_open_ticket(db, org_scope):
    org = await org_scope()
    case = await _make_session(
        db, org, ticket_status="open", conversation_status="open"
    )

    snapshot = await _snapshot_for(db, org)

    assert snapshot.closed_sessions_checked == 1
    assert [d.session_id for d in snapshot.drifted] == [case["session"].id]
    drifted = snapshot.drifted[0]
    assert drifted.ticket_id == case["ticket"].id
    assert drifted.conversation_id == case["conversation"].id
    assert drifted.ticket_status == "open"
    assert drifted.conversation_status == "open"
    assert drifted.resolved_at_present is False

    report = tool.render_dry_run(snapshot)
    assert str(case["session"].id) in report
    assert tool.DRY_RUN_FOOTER in report

    # Dry run never mutates.
    ticket = await _fresh_row(db, Ticket, case["ticket"].id)
    conversation = await _fresh_row(db, Conversation, case["conversation"].id)
    assert ticket.status == "open"
    assert conversation.status == "open"


@pytest.mark.asyncio
async def test_dry_run_reports_closed_session_with_open_conversation_only(db, org_scope):
    org = await org_scope()
    case = await _make_session(
        db,
        org,
        ticket_status="solved",
        conversation_status="open",
        resolved_at=datetime.now(UTC),
    )

    snapshot = await _snapshot_for(db, org)

    assert [d.session_id for d in snapshot.drifted] == [case["session"].id]
    drifted = snapshot.drifted[0]
    assert drifted.ticket_status == "solved"
    assert drifted.conversation_status == "open"
    assert drifted.resolved_at_present is True


@pytest.mark.asyncio
async def test_dry_run_reports_missing_resolution_milestone(db, org_scope):
    org = await org_scope()
    case = await _make_session(
        db,
        org,
        ticket_status="solved",
        conversation_status="closed",
        resolved_at=None,
    )

    snapshot = await _snapshot_for(db, org)

    assert [d.session_id for d in snapshot.drifted] == [case["session"].id]
    drifted = snapshot.drifted[0]
    assert drifted.ticket_status == "solved"
    assert drifted.conversation_status == "closed"
    assert drifted.resolved_at_present is False


@pytest.mark.asyncio
async def test_consistent_closed_session_not_reported(db, org_scope):
    org = await org_scope()
    case = await _make_session(
        db,
        org,
        ticket_status="solved",
        conversation_status="closed",
        resolved_at=datetime.now(UTC),
    )

    snapshot = await _snapshot_for(db, org)

    assert snapshot.closed_sessions_checked == 1
    assert snapshot.drifted == []
    report = tool.render_dry_run(snapshot)
    assert "drifted sessions: 0" in report
    assert str(case["session"].id) not in report


@pytest.mark.asyncio
async def test_live_sessions_are_never_candidates(db, org_scope):
    org = await org_scope()
    live = []
    for status in ("ai_active", "human_requested", "human_assigned"):
        case = await _make_session(
            db,
            org,
            session_status=status,
            ticket_status="open",
            conversation_status="open",
        )
        live.append(case)

    snapshot = await _snapshot_for(db, org)

    assert snapshot.closed_sessions_checked == 0
    assert snapshot.drifted == []

    result = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )
    assert result.drifted_before == 0
    assert result.applied == 0
    for case in live:
        session = await _fresh_row(db, PublicChatSession, case["session"].id)
        assert session.status in {"ai_active", "human_requested", "human_assigned"}


@pytest.mark.asyncio
async def test_tenant_a_cannot_inspect_or_reconcile_tenant_b(db, org_scope):
    suffix = uuid.uuid4().hex[:8]
    org_a = await org_scope(external_id=f"pc-drift-a-{suffix}")
    org_b = await org_scope(external_id=f"pc-drift-b-{suffix}")
    case_a = await _make_session(db, org_a, ticket_status="open")
    case_b = await _make_session(db, org_b, ticket_status="open")

    snapshot_a = await _snapshot_for(db, org_a)

    assert [d.session_id for d in snapshot_a.drifted] == [case_a["session"].id]
    assert case_b["session"].id not in [d.session_id for d in snapshot_a.drifted]

    result = await tool.run_apply(
        db, tenant_external_id=org_a.external_id, organization_id=org_a.id
    )
    assert result.applied == 1

    ticket_a = await _fresh_row(db, Ticket, case_a["ticket"].id)
    ticket_b = await _fresh_row(db, Ticket, case_b["ticket"].id)
    assert ticket_a.status == "solved"
    assert ticket_b.status == "open"


# ----------------------------------------------------------------------
# Apply / repair
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_repairs_drift_through_shared_lifecycle(db, org_scope, monkeypatch):
    org = await org_scope()
    case = await _make_session(
        db, org, ticket_status="open", conversation_status="open"
    )

    original = PublicChatResolutionService.close_lifecycle
    calls: list[tuple[int, int, str]] = []

    async def _recorded(db_, *, session, organization_id, when, closed_by):
        calls.append((session.id, organization_id, closed_by))
        return await original(
            db_,
            session=session,
            organization_id=organization_id,
            when=when,
            closed_by=closed_by,
        )

    monkeypatch.setattr(PublicChatResolutionService, "close_lifecycle", _recorded)

    result = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )

    assert calls == [(case["session"].id, org.id, "staff")]
    assert result.drifted_before == 1
    assert result.applied == 1
    assert result.drifted_after == 0

    ticket = await _fresh_row(db, Ticket, case["ticket"].id)
    conversation = await _fresh_row(db, Conversation, case["conversation"].id)
    assert ticket.status == "solved"
    assert ticket.resolved_at is not None
    assert conversation.status == "closed"


@pytest.mark.asyncio
async def test_apply_preserves_closed_at(db, org_scope):
    org = await org_scope()
    closed_at = datetime.now(UTC) - timedelta(days=3)
    case = await _make_session(
        db, org, ticket_status="open", closed_at=closed_at
    )

    await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )

    session = await _fresh_row(db, PublicChatSession, case["session"].id)
    assert session.status == "closed"
    assert session.closed_at == closed_at


@pytest.mark.asyncio
async def test_apply_preserves_conversation_messages(db, org_scope):
    org = await org_scope()
    case = await _make_session(db, org, ticket_status="open")
    await _make_message(db, case["conversation"], body="Please help me.")

    await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
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


@pytest.mark.asyncio
async def test_apply_is_idempotent_and_second_run_is_noop(db, org_scope):
    org = await org_scope()
    await _make_session(db, org, ticket_status="open", conversation_status="open")

    staff_before = _closed_total_for("staff")
    first = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )
    second = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )

    assert first.applied == 1
    assert first.drifted_after == 0
    assert second.drifted_before == 0
    assert second.applied == 0
    assert second.drifted_after == 0
    assert _closed_total_for("staff") == staff_before


@pytest.mark.asyncio
async def test_apply_creates_no_integration_job_or_agent_run(db, org_scope):
    org = await org_scope()
    await _make_session(db, org, ticket_status="open")

    assert await _count(db, IntegrationJob, org.id) == 0
    assert await _count(db, AgentRun, org.id) == 0

    await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )

    assert await _count(db, IntegrationJob, org.id) == 0
    assert await _count(db, AgentRun, org.id) == 0


@pytest.mark.asyncio
async def test_apply_emits_no_close_metric_or_log(db, org_scope, monkeypatch):
    org = await org_scope()
    await _make_session(db, org, ticket_status="open", conversation_status="open")

    recorder = _RecordingLogger()
    monkeypatch.setattr(resolution_module, "log", recorder)

    staff_before = _closed_total_for("staff")
    customer_before = _closed_total_for("customer")

    result = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )

    assert result.applied == 1
    assert _closed_total_for("staff") == staff_before
    assert _closed_total_for("customer") == customer_before
    assert [event for event, _ in recorder.events if event == "public_chat_session_closed"] == []


@pytest.mark.asyncio
async def test_apply_skips_session_that_changed_state(db, org_scope, monkeypatch):
    org = await org_scope()
    case = await _make_session(
        db, org, session_status="ai_active", ticket_status="open"
    )
    session_id = case["session"].id
    ticket_id = case["ticket"].id
    conversation_id = case["conversation"].id

    async def _stale_snapshot(db_, *, tenant_external_id, organization_id):
        return tool.DriftSnapshot(
            tenant_external_id=tenant_external_id,
            organization_id=organization_id,
            closed_sessions_checked=1,
            drifted=[
                tool.DriftedSession(
                    session_id=session_id,
                    ticket_id=ticket_id,
                    conversation_id=conversation_id,
                    ticket_status="open",
                    conversation_status="open",
                    resolved_at_present=False,
                )
            ],
        )

    monkeypatch.setattr(tool, "collect_drift_snapshot", _stale_snapshot)

    result = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )

    assert result.applied == 0
    assert result.skipped_state_changed == [session_id]

    session = await _fresh_row(db, PublicChatSession, session_id)
    ticket = await _fresh_row(db, Ticket, ticket_id)
    assert session.status == "ai_active"
    assert ticket.status == "open"


@pytest.mark.asyncio
async def test_apply_uses_for_update_locked_reread(db, org_scope):
    """Apply must re-read the session tenant-scoped under SELECT ... FOR UPDATE."""
    org = await org_scope()
    await _make_session(db, org, ticket_status="open", conversation_status="open")

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        result = await tool.run_apply(
            db, tenant_external_id=org.external_id, organization_id=org.id
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert result.applied == 1

    locked = [
        statement
        for statement in statements
        if "FOR UPDATE" in statement.upper() and "public_chat_sessions" in statement
    ]
    assert locked, "apply must lock the session row with SELECT ... FOR UPDATE"
    assert all("organization_id" in statement for statement in locked)
    assert any("public_chat_sessions.id" in statement for statement in locked)


@pytest.mark.asyncio
async def test_locked_reread_refreshes_stale_identity_map(db, org_scope):
    """A stale identity-map copy must not hide a concurrent status change."""
    org = await org_scope()
    case = await _make_session(db, org, ticket_status="open")
    session_id = case["session"].id

    # Prime db's identity map with the still-closed row, exactly as the
    # candidate snapshot would.
    primed = (
        await db.execute(
            select(PublicChatSession).where(PublicChatSession.id == session_id)
        )
    ).scalar_one()
    assert primed.status == "closed"

    # A concurrent writer flips the row out of "closed" and commits.
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(PublicChatSession)
            .where(PublicChatSession.id == session_id)
            .values(status="human_requested")
        )
        await other.commit()

    reread = await tool.get_locked_session_for_tenant(db, session_id, org.id)
    try:
        assert reread is not None
        # populate_existing overwrote the stale closed copy with the new value;
        # without it the identity map would hand back ``closed`` and the repair
        # would wrongly proceed.
        assert reread.status == "human_requested"
        assert reread is primed
    finally:
        await db.rollback()


@pytest.mark.asyncio
async def test_apply_skip_releases_locking_transaction(db, org_scope, monkeypatch):
    """The skip path must roll back so the FOR UPDATE lock is released."""
    org = await org_scope()
    case = await _make_session(
        db, org, session_status="ai_active", ticket_status="open"
    )
    session_id = case["session"].id
    ticket_id = case["ticket"].id
    conversation_id = case["conversation"].id

    async def _stale_snapshot(db_, *, tenant_external_id, organization_id):
        return tool.DriftSnapshot(
            tenant_external_id=tenant_external_id,
            organization_id=organization_id,
            closed_sessions_checked=1,
            drifted=[
                tool.DriftedSession(
                    session_id=session_id,
                    ticket_id=ticket_id,
                    conversation_id=conversation_id,
                    ticket_status="open",
                    conversation_status="open",
                    resolved_at_present=False,
                )
            ],
        )

    monkeypatch.setattr(tool, "collect_drift_snapshot", _stale_snapshot)

    result = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )

    assert result.applied == 0
    assert result.skipped_state_changed == [session_id]
    # The skip rolled the locking transaction back rather than leaving it open.
    assert db.in_transaction() is False

    # A second session can now take the row lock immediately; if the skip path
    # had leaked the lock this NOWAIT probe would raise instead of succeeding.
    async with AsyncSessionLocal() as probe:
        await probe.execute(
            select(PublicChatSession)
            .where(PublicChatSession.id == session_id)
            .with_for_update(nowait=True)
        )
        await probe.rollback()

    ticket = await _fresh_row(db, Ticket, ticket_id)
    session = await _fresh_row(db, PublicChatSession, session_id)
    assert ticket.status == "open"
    assert session.status == "ai_active"


# ----------------------------------------------------------------------
# Report safety and CLI
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_contains_no_sensitive_values(db, org_scope):
    org = await org_scope()
    suffix = uuid.uuid4().hex[:8]
    token_marker = f"TOKENHASH-{suffix}-" + "a" * 30
    widget_marker = f"WIDGETKEYHASH-{suffix}-" + "b" * 24
    body_marker = f"MESSAGE-BODY-{suffix}"
    email_marker = f"secret-{suffix}@example.com"

    case = await _make_session(
        db, org, ticket_status="open", token_hash=token_marker
    )
    config = case["config"]
    config.public_widget_key_hash = widget_marker
    await db.flush()
    case["ticket"].requester_email = email_marker
    await db.flush()
    await _make_message(db, case["conversation"], body=body_marker)
    await db.commit()

    snapshot = await _snapshot_for(db, org)
    report = tool.render_dry_run(snapshot)

    result = await tool.run_apply(
        db, tenant_external_id=org.external_id, organization_id=org.id
    )
    apply_report = tool.render_apply_result(result)

    combined = report + "\n" + apply_report
    for secret_value in (
        token_marker,
        widget_marker,
        body_marker,
        email_marker,
        settings.database_url,
    ):
        assert secret_value not in combined


@pytest.mark.asyncio
async def test_unknown_tenant_resolves_to_none(db):
    assert (
        await tool.resolve_organization_id(db, f"does-not-exist-{uuid.uuid4().hex}")
        is None
    )


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


def test_parse_args_rejects_apply_with_dry_run():
    with pytest.raises(SystemExit):
        tool.parse_args(["--tenant", "rispu", "--apply", "--dry-run"])
