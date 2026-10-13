"""Phase 1P.7.3B — bounded, cross-tenant scheduled expiry finalization.

Covers ``PublicChatExpiryService.finalize_expired_batch`` and its SQL seams over
the real database:

- tenants are discovered server-side with a bounded SQL query ordered by
  oldest pending expiry, and capacity is shared fairly, so a low-id tenant with
  a sustained backlog cannot starve a higher-id tenant;
- the candidate batch limit is a SQL ``LIMIT`` (never a Python slice) and is
  exact across tenants;
- a close whose SLA milestone failed AFTER the core commit is counted as
  finalized and later repaired through the shared lifecycle, with ``closed_at``
  preserved and no second close metric;
- the scheduled pass never waits on a locked session row (``SKIP LOCKED``) and
  every other lock wait is bounded by a LOCAL ``lock_timeout``;
- failures are never reported as ``complete``;
- tenant isolation, the tenant-scoped ``FOR UPDATE`` re-read, revalidation,
  ``closed_at == expires_at``, and the shared close lifecycle are preserved;
- concurrent/duplicate runs finalize each session exactly once;
- the deadline (``should_stop``) defers work without abandoning a lock;
- a failing tenant is isolated and does not abort the batch;
- an empty batch is a successful no-op.

Determinism: every fixture session expires in a fixed instant in the year 2001
and every batch runs with ``now`` pinned just after it. Discovery is global by
design, so pinning ``now`` far in the past is what keeps unrelated rows in a
shared development database out of every assertion.
"""

import asyncio
import itertools
import os
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from prometheus_client import generate_latest
from sqlalchemy import delete, event, func, select, text, update

os.environ["ENVIRONMENT"] = "development"

from app.core.database import AsyncSessionLocal, engine
from app.core.metrics import PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL
from app.models.agent_run import AgentRun
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.models.ticket import Ticket
from app.services import public_chat_expiry_service as expiry_module
from app.services.public_chat_expiry_service import (
    MAX_EXPIRY_BATCH_LIMIT,
    PublicChatExpiryService,
    allocate_fair_shares,
    bounded_session,
    rotation_rank,
)
from app.services.public_chat_resolution import PublicChatResolutionService
from app.services.public_chat_service import hash_digest
from app.services.ticket_sla_service import TicketSLAService

BATCH_ORG_PREFIX = "pc-expiry-batch-test-"

# Every fixture session expires at BASE_EXPIRY + n seconds; batches run at NOW.
BASE_EXPIRY = datetime(2001, 1, 1, tzinfo=UTC)
NOW = BASE_EXPIRY + timedelta(days=1)

_PURGE_TABLES = [
    "ticket_events",
    "agent_action_events",
    "ai_usage_reservations",
    "ai_request_logs",
    "integration_jobs",
    "agent_runs",
    "conversation_messages",
    "public_chat_sessions",
    "public_chat_configurations",
    "conversations",
    "tickets",
]


async def _purge_org_rows(session, org_ids) -> None:
    if not org_ids:
        return
    for table_name in _PURGE_TABLES:
        table = Base.metadata.tables.get(table_name)
        if table is not None and "organization_id" in table.columns:
            await session.execute(
                delete(table).where(table.c.organization_id.in_(org_ids))
            )
    await session.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await session.commit()


async def _namespace_org_ids(session) -> list[int]:
    return [
        org_id
        for (org_id,) in await session.execute(
            select(Organization.id).where(
                Organization.name.like(f"{BATCH_ORG_PREFIX}%")
            )
        )
    ]


async def _purge_namespace() -> None:
    async with AsyncSessionLocal() as session:
        await _purge_org_rows(session, await _namespace_org_ids(session))


async def _purge_fixed_epoch_strays() -> None:
    """Close nothing, delete nothing outside our namespace - just assert.

    Any live session from another namespace that expired before ``NOW`` would
    be discovered by these tests. None should exist; fail loudly if one does
    rather than silently finalizing a foreign row.
    """
    async with AsyncSessionLocal() as session:
        stray = (
            await session.execute(
                select(func.count())
                .select_from(PublicChatSession)
                .join(Organization, Organization.id == PublicChatSession.organization_id)
                .where(
                    PublicChatSession.expires_at <= NOW,
                    PublicChatSession.status != "closed",
                    Organization.name.notlike(f"{BATCH_ORG_PREFIX}%"),
                )
            )
        ).scalar_one()
        assert stray == 0, "foreign live sessions expired before the fixed test epoch"


@pytest.fixture(scope="module", autouse=True)
def _namespace_isolated():
    asyncio.run(_purge_namespace())
    asyncio.run(_purge_fixed_epoch_strays())
    yield
    asyncio.run(_purge_namespace())


@pytest_asyncio.fixture
async def org_scope(db):
    org_ids: list[int] = []

    async def make() -> int:
        suffix = uuid.uuid4().hex[:10]
        org = Organization(
            name=f"{BATCH_ORG_PREFIX}{suffix}",
            external_id=f"pc-expiry-batch-{suffix}",
        )
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        await db.commit()
        return org.id

    yield make

    await db.rollback()
    await _purge_org_rows(db, org_ids)


_seq = 0


async def _make_session(
    db,
    org_id: int,
    *,
    session_status: str = "ai_active",
    expires_at: datetime | None = None,
) -> dict[str, int | datetime]:
    """Insert one session + linked ticket/conversation; returns plain ids."""
    global _seq
    _seq += 1
    config = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org_id
            )
        )
    ).scalar_one_or_none()
    if config is None:
        config = PublicChatConfiguration(
            organization_id=org_id,
            public_widget_key_hash=hash_digest(f"pk_live_{secrets.token_hex(20)}"),
            display_name="Acme Support",
            welcome_message="Hi!",
            allowed_origins=["https://widget.example.test"],
            enabled=True,
            max_message_length=4000,
            max_messages_per_minute=20,
            session_ttl_hours=24,
        )
        db.add(config)
        await db.flush()

    if expires_at is None:
        expires_at = BASE_EXPIRY + timedelta(seconds=_seq)

    conversation = Conversation(
        organization_id=org_id,
        provider="public_chat",
        channel="public_chat",
        status="open",
    )
    ticket = Ticket(
        organization_id=org_id,
        subject="Widget inquiry",
        description=".",
        status="new",
        priority="medium",
        source="public-chat-widget",
    )
    db.add_all([conversation, ticket])
    await db.flush()

    session = PublicChatSession(
        organization_id=org_id,
        configuration_id=config.id,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        token_hash=hash_digest(secrets.token_hex(32)),
        status=session_status,
        expires_at=expires_at,
        closed_at=expires_at if session_status == "closed" else None,
    )
    db.add(session)
    await db.commit()
    return {
        "session_id": session.id,
        "ticket_id": ticket.id,
        "conversation_id": conversation.id,
        "expires_at": expires_at,
    }


async def _fresh(model, row_id: int):
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(select(model).where(model.id == row_id))
        ).scalar_one()


async def _status_of(session_ids: list[int]) -> dict[int, str]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(PublicChatSession.id, PublicChatSession.status).where(
                PublicChatSession.id.in_(session_ids)
            )
        )
        return {row_id: status for row_id, status in rows.all()}


def _closed_total(closed_by: str = "expiry") -> int:
    prefix = f'cxops_public_chat_sessions_closed_total{{closed_by="{closed_by}"}}'
    total = 0.0
    for line in generate_latest(PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL).decode().splitlines():
        if line.startswith(prefix):
            total += float(line.rsplit(" ", 1)[-1])
    return int(total)


async def _batch(limit: int, **kwargs):
    kwargs.setdefault("rotation_index", 1)
    return await PublicChatExpiryService.finalize_expired_batch(
        AsyncSessionLocal, limit=limit, now=NOW, **kwargs
    )


def _turn(*, repair: bool) -> int:
    """A rotation index whose ``limit == 1`` class turn is repair (or not)."""
    for index in range(1, 100):
        if (rotation_rank(index, 2, expiry_module._CLASS_ROTATION) == 0) == repair:
            return index
    raise AssertionError("no matching turn")


def _fail_lifecycle_for(monkeypatch, predicate):
    """Make ``close_lifecycle`` raise (before any write) when predicate(session)."""
    original = PublicChatResolutionService.close_lifecycle.__func__

    async def _maybe_fail(cls, db_, *, session, **kwargs):
        if predicate(session):
            raise RuntimeError("postgresql://user:secret@host/db injected")
        return await original(cls, db_, session=session, **kwargs)

    monkeypatch.setattr(
        PublicChatResolutionService, "close_lifecycle", classmethod(_maybe_fail)
    )


async def _make_repair_candidate(db, org_id: int) -> dict:
    """An expiry close whose SLA milestone never landed (partial commit)."""
    case = await _make_session(db, org_id)
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(PublicChatSession)
            .where(PublicChatSession.id == case["session_id"])
            .values(status="closed", closed_at=case["expires_at"])
        )
        await other.execute(
            update(Ticket).where(Ticket.id == case["ticket_id"]).values(status="solved")
        )
        await other.commit()
    return case


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def info(self, event_name, **kwargs):
        self.events.append(("info", event_name, kwargs))

    def error(self, event_name, **kwargs):
        self.events.append(("error", event_name, kwargs))

    def of(self, event_name):
        return [kw for _lvl, name, kw in self.events if name == event_name]


# ----------------------------------------------------------------------
# Empty batch + bounds
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_batch_is_a_successful_noop():
    result = await _batch(10)

    assert result.candidates_attempted == 0
    assert result.finalized == 0
    assert result.exit_reason == "complete"


@pytest.mark.parametrize("bad", [0, -1, MAX_EXPIRY_BATCH_LIMIT + 1])
@pytest.mark.asyncio
async def test_batch_rejects_out_of_range_limit(bad):
    with pytest.raises(ValueError):
        await _batch(bad)


@pytest.mark.parametrize("bad", [0, -5])
@pytest.mark.asyncio
async def test_batch_rejects_non_positive_timeouts(bad):
    with pytest.raises(ValueError):
        await _batch(10, lock_timeout_ms=bad)


@pytest.mark.parametrize(
    "alpha_name", ["_TENANT_ROTATION", "_ROW_ROTATION", "_REPAIR_ROTATION"]
)
@pytest.mark.parametrize("step", [1, 2, 5])  # run cadence relative to the bucket
def test_rotation_rank_is_bounded_and_covers_every_rank(alpha_name, step):
    alpha = getattr(expiry_module, alpha_name)
    for count in range(1, 101):
        ranks = [rotation_rank(n * step, count, alpha) for n in range(1, 8 * count + 2)]
        assert all(0 <= r < count for r in ranks)
        assert set(ranks) == set(range(count)), (alpha_name, step, count)


@pytest.mark.parametrize("step", [1, 2, 3, 5, 7, 12])
def test_class_turns_alternate_for_any_cadence(step):
    turns = [
        rotation_rank(n * step, 2, expiry_module._CLASS_ROTATION) for n in range(1, 2001)
    ]
    longest = current = 1
    for prev, cur in itertools.pairwise(turns):
        current = current + 1 if cur == prev else 1
        longest = max(longest, current)
    assert longest <= 5


def test_fair_share_allocation_is_bounded_and_gives_everyone_a_turn():
    assert allocate_fair_shares([100, 1], 4) == [3, 1]
    assert allocate_fair_shares([3, 3], 4) == [2, 2]
    assert allocate_fair_shares([1, 1, 1], 2) == [1, 1, 0]
    assert allocate_fair_shares([2, 1], 10) == [2, 1]
    assert allocate_fair_shares([], 5) == []
    assert sum(allocate_fair_shares([50, 50, 50], 7)) == 7


# ----------------------------------------------------------------------
# Server-side discovery + lock-aware SQL
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_is_server_side_grouped_ordered_and_bounded(db, org_scope):
    org_a = await org_scope()
    org_b = await org_scope()
    org_c = await org_scope()
    # Oldest pending expiry: C, then A, then B - deliberately not id order.
    await _make_session(db, org_c, expires_at=BASE_EXPIRY + timedelta(minutes=1))
    await _make_session(db, org_a, expires_at=BASE_EXPIRY + timedelta(minutes=2))
    await _make_session(db, org_a, expires_at=BASE_EXPIRY + timedelta(minutes=9))
    await _make_session(db, org_b, expires_at=BASE_EXPIRY + timedelta(minutes=3))
    org_d = await org_scope()
    await _make_session(db, org_d, expires_at=NOW + timedelta(hours=1))
    await _make_session(db, org_d, session_status="closed")

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        async with AsyncSessionLocal() as session:
            discover = PublicChatExpiryService.discover_tenants_with_expired_sessions
            all_ids = await discover(session, now=NOW, limit=10)
            bounded = await discover(session, now=NOW, limit=2)
            excluded = await discover(session, now=NOW, limit=10, exclude=[org_c])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert all_ids == [(org_c, 1), (org_a, 2), (org_b, 1)]
    assert bounded == [(org_c, 1), (org_a, 2)]
    assert excluded == [(org_a, 2), (org_b, 1)]
    for sql in statements:
        assert "GROUP BY" in sql.upper() and "LIMIT" in sql.upper()
        assert "min(public_chat_sessions.expires_at)" in sql


@pytest.mark.asyncio
async def test_row_selection_is_lock_aware_ordered_and_limit_one(db, org_scope):
    org_id = await org_scope()
    for _ in range(3):
        await _make_session(db, org_id)

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        result = await _batch(10)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert result.finalized == 3
    locking = [
        s for s in statements
        if "FOR UPDATE" in s.upper() and "FROM public_chat_sessions" in s
    ]
    assert locking
    for sql in locking:
        assert "SKIP LOCKED" in sql.upper()
        assert "LIMIT" in sql.upper()
        assert "ORDER BY public_chat_sessions.expires_at, public_chat_sessions.id" in sql
        assert "public_chat_sessions.organization_id" in sql
    # No multi-row candidate query ever runs without a LIMIT (single-row
    # primary-key reads inside the shared lifecycle are bounded by definition).
    for sql in statements:
        if sql.lstrip().upper().startswith("SELECT") and "public_chat_sessions" in sql:
            assert (
                "LIMIT" in sql.upper()
                or "count(" in sql.lower()
                or "public_chat_sessions.id = $" in sql
            ), sql


# ----------------------------------------------------------------------
# Exact bounds
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_limit_is_exact_across_tenants(db, org_scope):
    org_a = await org_scope()
    org_b = await org_scope()
    a_ids = [(await _make_session(db, org_a))["session_id"] for _ in range(3)]
    b_ids = [(await _make_session(db, org_b))["session_id"] for _ in range(3)]

    result = await _batch(4)

    assert result.candidates_attempted == 4
    assert result.finalized == 4
    assert result.exit_reason == "batch_limit"
    statuses = await _status_of(a_ids + b_ids)
    closed_a = sum(statuses[i] == "closed" for i in a_ids)
    closed_b = sum(statuses[i] == "closed" for i in b_ids)
    assert closed_a + closed_b == 4
    assert closed_a >= 1 and closed_b >= 1

    follow_up = await _batch(4)
    assert follow_up.finalized == 2
    assert follow_up.exit_reason == "complete"
    assert set((await _status_of(a_ids + b_ids)).values()) == {"closed"}


@pytest.mark.parametrize("rotation_index", [1, 2, 3, 4])
@pytest.mark.asyncio
async def test_batch_limit_of_one_finalizes_at_most_one(db, org_scope, rotation_index):
    org_id = await org_scope()
    ids = [(await _make_session(db, org_id))["session_id"] for _ in range(3)]

    result = await _batch(1, rotation_index=rotation_index)

    assert result.candidates_attempted == 1
    assert result.finalized == 1
    assert list((await _status_of(ids)).values()).count("closed") == 1


# ----------------------------------------------------------------------
# Tenant isolation + lifecycle preservation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_preserves_lifecycle_and_tenant_scoping(db, org_scope):
    org_a = await org_scope()
    org_b = await org_scope()
    case_a = await _make_session(db, org_a, session_status="human_assigned")
    case_b = await _make_session(db, org_b, session_status="human_requested")
    live_b = await _make_session(db, org_b, expires_at=NOW + timedelta(hours=1))

    result = await _batch(10)

    assert result.finalized == 2
    for case in (case_a, case_b):
        session = await _fresh(PublicChatSession, case["session_id"])
        ticket = await _fresh(Ticket, case["ticket_id"])
        conversation = await _fresh(Conversation, case["conversation_id"])
        assert session.status == "closed"
        assert session.closed_at == case["expires_at"]
        assert ticket.status == "solved"
        assert ticket.resolved_at is not None
        assert conversation.status == "closed"
    assert (await _fresh(PublicChatSession, live_b["session_id"])).status == "ai_active"

    async with AsyncSessionLocal() as session:
        for model in (IntegrationJob, AgentRun):
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.organization_id.in_([org_a, org_b]))
                )
            ).scalar_one()
            assert count == 0


# ----------------------------------------------------------------------
# (d) Duplicate runs, concurrent workers, races
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_runs_are_idempotent(db, org_scope):
    org_id = await org_scope()
    await _make_session(db, org_id)
    await _make_session(db, org_id)

    before = _closed_total()
    first = await _batch(10)
    second = await _batch(10)

    assert first.finalized == 2
    assert second.finalized == 0
    assert second.candidates_attempted == 0
    assert _closed_total() == before + 2


@pytest.mark.parametrize("indexes", [(1, 1), (1, 2), (3, 7)])
@pytest.mark.asyncio
async def test_concurrent_workers_finalize_each_session_exactly_once(
    db, org_scope, indexes
):
    org_a = await org_scope()
    org_b = await org_scope()
    ids = []
    for org_id in (org_a, org_b):
        for _ in range(3):
            ids.append((await _make_session(db, org_id))["session_id"])

    before = _closed_total()
    first, second = await asyncio.gather(
        _batch(10, rotation_index=indexes[0]), _batch(10, rotation_index=indexes[1])
    )

    assert set((await _status_of(ids)).values()) == {"closed"}
    assert first.finalized + second.finalized == len(ids)
    assert _closed_total() == before + len(ids)
    assert first.outcome == second.outcome == "ok"


@pytest.mark.asyncio
async def test_concurrent_repairs_repair_once_without_close_metric(db, org_scope):
    org_id = await org_scope()
    cases = [await _make_repair_candidate(db, org_id) for _ in range(2)]
    before = _closed_total()

    first, second = await asyncio.gather(_batch(10), _batch(10, rotation_index=2))

    assert first.repaired + second.repaired == 2
    assert _closed_total() == before
    for case in cases:
        assert (await _fresh(Ticket, case["ticket_id"])).resolved_at is not None
        assert (
            await _fresh(PublicChatSession, case["session_id"])
        ).closed_at == case["expires_at"]
    assert (await _batch(10)).repaired == 0


@pytest.mark.asyncio
async def test_session_closed_concurrently_is_not_reclosed(db, org_scope, monkeypatch):
    org_id = await org_scope()
    case = await _make_session(db, org_id)
    original = PublicChatExpiryService._rotation_pivot

    async def _pivot_then_race(db_, **kwargs):
        pivot = await original(db_, **kwargs)
        async with AsyncSessionLocal() as other:  # a concurrent staff close
            await other.execute(
                update(PublicChatSession)
                .where(PublicChatSession.id == case["session_id"])
                .values(status="closed", closed_at=NOW)
            )
            await other.commit()
        return pivot

    monkeypatch.setattr(PublicChatExpiryService, "_rotation_pivot", _pivot_then_race)
    before = _closed_total()
    result = await _batch(10)

    assert result.finalized == 0
    assert result.outcome == "ok"
    session = await _fresh(PublicChatSession, case["session_id"])
    assert session.closed_at == NOW  # the concurrent writer's value, untouched
    assert _closed_total() == before


@pytest.mark.asyncio
async def test_expiry_extended_concurrently_is_not_finalized(db, org_scope, monkeypatch):
    org_id = await org_scope()
    case = await _make_session(db, org_id)
    original = PublicChatExpiryService._rotation_pivot

    async def _pivot_then_extend(db_, **kwargs):
        pivot = await original(db_, **kwargs)
        async with AsyncSessionLocal() as other:
            await other.execute(
                update(PublicChatSession)
                .where(PublicChatSession.id == case["session_id"])
                .values(expires_at=NOW + timedelta(hours=24))
            )
            await other.commit()
        return pivot

    monkeypatch.setattr(PublicChatExpiryService, "_rotation_pivot", _pivot_then_extend)
    result = await _batch(10)

    assert result.finalized == 0
    assert (await _fresh(PublicChatSession, case["session_id"])).status == "ai_active"
    assert (await _fresh(Ticket, case["ticket_id"])).status == "new"


# ----------------------------------------------------------------------
# Deadline
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deadline_already_passed_does_no_work(db, org_scope):
    org_id = await org_scope()
    case = await _make_session(db, org_id)

    result = await _batch(10, should_stop=lambda: True)

    assert result.exit_reason == "deadline"
    assert result.candidates_attempted == 0
    assert (await _fresh(PublicChatSession, case["session_id"])).status == "ai_active"


@pytest.mark.asyncio
async def test_deadline_mid_batch_stops_between_rows(db, org_scope, monkeypatch):
    org_id = await org_scope()
    ids = [(await _make_session(db, org_id))["session_id"] for _ in range(4)]
    original = PublicChatResolutionService.close_lifecycle.__func__
    closes = {"n": 0}

    async def _counting(cls, db_, **kwargs):
        resolution = await original(cls, db_, **kwargs)
        closes["n"] += 1
        return resolution

    monkeypatch.setattr(
        PublicChatResolutionService, "close_lifecycle", classmethod(_counting)
    )

    result = await _batch(10, should_stop=lambda: closes["n"] >= 2)

    assert result.exit_reason == "deadline"
    assert result.finalized == 2
    assert list((await _status_of(ids)).values()).count("closed") == 2


# ----------------------------------------------------------------------
# Failure reporting
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_tenant_is_isolated_and_reported_as_partial_failure(
    db, org_scope, monkeypatch
):
    org_a = await org_scope()
    org_b = await org_scope()
    failing, healthy = sorted([org_a, org_b])
    await _make_session(db, failing)
    healthy_case = await _make_session(db, healthy)
    _fail_lifecycle_for(monkeypatch, lambda s: s.organization_id == failing)
    recorder = _Recorder()
    monkeypatch.setattr(expiry_module, "log", recorder)

    result = await _batch(10)

    assert result.tenants_failed == [failing]
    assert result.finalized == 1
    assert result.outcome == "partial_failure"
    assert result.exit_reason == "partial_failure"
    assert set(result.failure_kinds) == {"RuntimeError"}
    assert (await _fresh(PublicChatSession, healthy_case["session_id"])).status == "closed"
    failures = recorder.of("public_chat_expiry_candidate_failed")
    assert failures and all(
        f["organization_id"] == failing and f["failure_kind"] == "RuntimeError"
        for f in failures
    )
    assert "secret" not in repr(recorder.events)


@pytest.mark.asyncio
async def test_all_tenants_failing_is_reported_as_failed(db, org_scope, monkeypatch):
    org_id = await org_scope()
    await _make_session(db, org_id)
    _fail_lifecycle_for(monkeypatch, lambda _s: True)

    result = await _batch(10)

    assert result.outcome == "failed"
    assert result.exit_reason == "failed"
    assert result.finalized == 0


# ----------------------------------------------------------------------
# (a) Tenant starvation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sustained_low_id_backlog_cannot_starve_a_higher_id_tenant(
    db, org_scope
):
    low = await org_scope()
    high = await org_scope()
    for minute in range(8):
        await _make_session(db, low, expires_at=BASE_EXPIRY + timedelta(minutes=minute))
    high_case = await _make_session(
        db, high, expires_at=BASE_EXPIRY + timedelta(minutes=30)
    )

    result = await _batch(2)

    assert result.finalized == 2
    assert (await _fresh(PublicChatSession, high_case["session_id"])).status == "closed"


@pytest.mark.asyncio
async def test_two_permanently_failing_oldest_tenants_cannot_starve_a_healthy_one(
    db, org_scope, monkeypatch
):
    """Batch size 2; both FIFO-first tenants fail forever. The rotation slot
    still reaches the newer healthy tenant within a bounded number of runs."""
    fail_a = await org_scope()
    fail_b = await org_scope()
    healthy = await org_scope()
    for org_id in (fail_a, fail_b):
        for minute in range(3):
            await _make_session(
                db, org_id, expires_at=BASE_EXPIRY + timedelta(minutes=minute)
            )
    healthy_case = await _make_session(
        db, healthy, expires_at=BASE_EXPIRY + timedelta(hours=5)
    )
    _fail_lifecycle_for(monkeypatch, lambda s: s.organization_id in (fail_a, fail_b))

    reached_on = None
    for run in range(1, 10):
        result = await _batch(2, rotation_index=run)
        assert result.candidates_attempted <= 2
        if (await _fresh(PublicChatSession, healthy_case["session_id"])).status == "closed":
            reached_on = run
            break

    # rotation_rank(3, 3) == 2: the healthy tenant (highest id) on run 3.
    assert reached_on == 3
    assert result.outcome == "partial_failure"


@pytest.mark.asyncio
async def test_more_failing_tenants_than_slots_still_reach_healthy_tenant(
    db, org_scope, monkeypatch
):
    failing = [await org_scope() for _ in range(4)]
    healthy = await org_scope()
    for org_id in failing:
        await _make_session(db, org_id, expires_at=BASE_EXPIRY)
    healthy_case = await _make_session(
        db, healthy, expires_at=BASE_EXPIRY + timedelta(hours=5)
    )
    _fail_lifecycle_for(monkeypatch, lambda s: s.organization_id in failing)

    for run in range(1, 30):
        await _batch(2, rotation_index=run * 5)  # 5-minute cadence
        if (await _fresh(PublicChatSession, healthy_case["session_id"])).status == "closed":
            break
    else:
        raise AssertionError("healthy tenant starved behind failing tenants")


@pytest.mark.asyncio
async def test_more_tenants_than_capacity_rotate_by_oldest_pending_expiry(
    db, org_scope
):
    low_a = await org_scope()
    low_b = await org_scope()
    high = await org_scope()
    for minute in range(3):
        await _make_session(db, low_a, expires_at=BASE_EXPIRY + timedelta(minutes=minute))
        await _make_session(db, low_b, expires_at=BASE_EXPIRY + timedelta(minutes=minute))
    high_case = await _make_session(
        db, high, expires_at=BASE_EXPIRY + timedelta(minutes=2, seconds=30)
    )

    reached_on = None
    for run in range(1, 8):
        await _batch(2, rotation_index=run)
        if (await _fresh(PublicChatSession, high_case["session_id"])).status == "closed":
            reached_on = run
            break
        for org_id in (low_a, low_b):  # sustained low-id backlog
            await _make_session(
                db, org_id, expires_at=BASE_EXPIRY + timedelta(hours=1, minutes=run)
            )

    assert reached_on is not None and reached_on <= 4


# ----------------------------------------------------------------------
# (b) Locked head rows
# ----------------------------------------------------------------------


@pytest.mark.parametrize("rotation_index", [1, 2, 3, 4])
@pytest.mark.asyncio
async def test_locked_oldest_rows_do_not_block_newer_rows(
    db, org_scope, rotation_index
):
    org_id = await org_scope()
    cases = [await _make_session(db, org_id) for _ in range(5)]
    locked_ids = [c["session_id"] for c in cases[:3]]
    free_ids = [c["session_id"] for c in cases[3:]]

    async with AsyncSessionLocal() as holder:
        await holder.execute(
            select(PublicChatSession)
            .where(PublicChatSession.id.in_(locked_ids))
            .with_for_update()
        )
        started = time.monotonic()
        # Two passes while the oldest rows stay locked the whole time.
        first = await _batch(2, rotation_index=rotation_index)
        second = await _batch(2, rotation_index=rotation_index + 1)
        elapsed = time.monotonic() - started
        await holder.rollback()

    assert elapsed < 3.0, "SKIP LOCKED must never wait for the holder"
    assert first.finalized + second.finalized == 2
    assert first.outcome == second.outcome == "ok"
    statuses = await _status_of(locked_ids + free_ids)
    assert all(statuses[i] == "closed" for i in free_ids)
    assert all(statuses[i] == "ai_active" for i in locked_ids)

    # Released: the formerly locked rows finalize normally.
    assert (await _batch(10)).finalized == 3


@pytest.mark.asyncio
async def test_blocked_ticket_lock_times_out_and_rolls_back(db, org_scope):
    org_id = await org_scope()
    case = await _make_session(db, org_id)

    async with AsyncSessionLocal() as holder:
        await holder.execute(
            select(Ticket).where(Ticket.id == case["ticket_id"]).with_for_update()
        )
        started = time.monotonic()
        result = await _batch(10, lock_timeout_ms=200)
        elapsed = time.monotonic() - started
        await holder.rollback()

    assert elapsed < 5.0, "lock_timeout must bound the wait"
    assert result.failure_kinds == {"sqlstate_55P03": 1}  # never retried in-run
    assert result.finalized == 0
    assert result.pending_milestone == 0
    assert result.outcome == "failed"
    assert (await _fresh(PublicChatSession, case["session_id"])).status == "ai_active"
    assert (await _fresh(Ticket, case["ticket_id"])).status == "new"

    assert (await _batch(10)).finalized == 1


@pytest.mark.asyncio
async def test_timeouts_are_transaction_local_and_reapplied_after_commit():
    async with bounded_session(
        AsyncSessionLocal, lock_timeout_ms=321, statement_timeout_ms=4321
    ) as session:
        first = (await session.execute(text("SHOW lock_timeout"))).scalar_one()
        stmt = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
        await session.commit()
        after_commit = (await session.execute(text("SHOW lock_timeout"))).scalar_one()
        await session.rollback()

    async with AsyncSessionLocal() as plain:
        default = (await plain.execute(text("SHOW lock_timeout"))).scalar_one()

    assert first == "321ms"
    assert stmt == "4321ms"
    assert after_commit == "321ms"
    assert default != "321ms"


# ----------------------------------------------------------------------
# Partial commit (core committed, SLA milestone failed) + repair
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_after_core_commit_is_counted_and_later_repaired(
    db, org_scope, monkeypatch
):
    org_id = await org_scope()
    cases = [await _make_session(db, org_id) for _ in range(3)]

    original = TicketSLAService.record_resolution.__func__
    calls = {"n": 0}

    async def _fail_second(cls, db_, ticket, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("sla follow-up failed")
        return await original(cls, db_, ticket, **kwargs)

    monkeypatch.setattr(TicketSLAService, "record_resolution", classmethod(_fail_second))
    metric_before = _closed_total()

    degraded = await _batch(10)

    # The second close committed its core transition, then the SLA follow-up
    # raised. It is counted as finalized (no under-reporting) and pending; the
    # tenant then backs off for this run, so the third waits for the next run.
    assert degraded.candidates_attempted == 2
    assert degraded.finalized == 2
    assert degraded.pending_milestone == 1
    assert degraded.outcome == "partial_failure"
    assert _closed_total() == metric_before + 2
    pending = []
    for c in cases:
        ticket = await _fresh(Ticket, c["ticket_id"])
        if ticket.status == "solved" and ticket.resolved_at is None:
            pending.append(c)
    assert len(pending) == 1
    middle = pending[0]
    session = await _fresh(PublicChatSession, middle["session_id"])
    assert session.status == "closed"
    assert session.closed_at == middle["expires_at"]

    monkeypatch.setattr(TicketSLAService, "record_resolution", classmethod(original))
    repaired = await _batch(10)

    assert repaired.repaired == 1
    assert repaired.finalized == 1  # the backed-off third session
    assert repaired.outcome == "ok"
    assert _closed_total() == metric_before + 3  # no duplicate close signal
    assert (await _fresh(PublicChatSession, middle["session_id"])).closed_at == middle[
        "expires_at"
    ]
    assert (await _fresh(Ticket, middle["ticket_id"])).resolved_at is not None
    assert (await _batch(10)).repaired == 0


@pytest.mark.asyncio
async def test_repair_ignores_non_expiry_closes(db, org_scope):
    """A staff close (closed_at != expires_at) is not this path's to repair."""
    org_id = await org_scope()
    case = await _make_session(db, org_id)
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(PublicChatSession)
            .where(PublicChatSession.id == case["session_id"])
            .values(status="closed", closed_at=NOW)
        )
        await other.execute(
            update(Ticket).where(Ticket.id == case["ticket_id"]).values(status="solved")
        )
        await other.commit()

    result = await _batch(10)

    assert result.repaired == 0
    assert result.candidates_attempted == 0
    assert (await _fresh(Ticket, case["ticket_id"])).resolved_at is None


# ----------------------------------------------------------------------
# (c) Repair capacity: failing / locked repairs vs live expiries
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_repair_cannot_starve_live_expiry_with_batch_size_one(
    db, org_scope, monkeypatch
):
    org_id = await org_scope()
    repair_case = await _make_repair_candidate(db, org_id)
    live_ids = [(await _make_session(db, org_id))["session_id"] for _ in range(3)]
    _fail_lifecycle_for(monkeypatch, lambda s: s.id == repair_case["session_id"])

    repair_attempts = 0
    for run in range(1, 20):
        result = await _batch(1, rotation_index=run)
        assert result.candidates_attempted <= 1
        repair_attempts += result.failure_kinds.get("RuntimeError", 0)
        if set((await _status_of(live_ids)).values()) == {"closed"}:
            break
    else:
        raise AssertionError("live expiries starved behind a failing repair")

    assert repair_attempts >= 1  # the repair class kept its turns too


@pytest.mark.asyncio
async def test_failing_repairs_never_take_all_capacity_when_batch_is_larger(
    db, org_scope, monkeypatch
):
    org_id = await org_scope()
    repairs = [await _make_repair_candidate(db, org_id) for _ in range(3)]
    live_ids = [(await _make_session(db, org_id))["session_id"] for _ in range(4)]
    failing_ids = {c["session_id"] for c in repairs}
    _fail_lifecycle_for(monkeypatch, lambda s: s.id in failing_ids)

    result = await _batch(5)

    # Repair capacity is max(1, 5 // 5) == 1; the other 4 units go to live work.
    assert result.failures == 1
    assert result.finalized == 4
    assert set((await _status_of(live_ids)).values()) == {"closed"}


@pytest.mark.asyncio
async def test_failing_head_repair_does_not_block_other_repairs(
    db, org_scope, monkeypatch
):
    org_id = await org_scope()
    head = await _make_repair_candidate(db, org_id)
    others = [await _make_repair_candidate(db, org_id) for _ in range(2)]
    _fail_lifecycle_for(monkeypatch, lambda s: s.id == head["session_id"])

    for run in range(1, 20):
        await _batch(2, rotation_index=run)
        resolved = [(await _fresh(Ticket, c["ticket_id"])).resolved_at for c in others]
        if all(value is not None for value in resolved):
            break
    else:
        raise AssertionError("a failing head repair starved the other repairs")
    assert (await _fresh(Ticket, head["ticket_id"])).resolved_at is None


@pytest.mark.asyncio
async def test_locked_repair_candidate_yields_capacity_to_live_expiry(db, org_scope):
    org_id = await org_scope()
    repair_case = await _make_repair_candidate(db, org_id)
    live = await _make_session(db, org_id)

    async with AsyncSessionLocal() as holder:
        await holder.execute(
            select(PublicChatSession)
            .where(PublicChatSession.id == repair_case["session_id"])
            .with_for_update()
        )
        # A repair turn with a single unit of capacity.
        result = await _batch(1, rotation_index=_turn(repair=True))
        await holder.rollback()

    assert result.repaired == 0
    assert result.finalized == 1
    assert (await _fresh(PublicChatSession, live["session_id"])).status == "closed"
    assert (await _fresh(Ticket, repair_case["ticket_id"])).resolved_at is None

    repaired = await _batch(1, rotation_index=_turn(repair=True))
    assert repaired.repaired == 1
