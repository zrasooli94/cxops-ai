"""Phase 1P.9R — database connection ownership across model calls.

The defect: a chat turn held its request/job session connection for the whole
analysis while ``analyze`` checked out a *second* pooled connection for its
advisory lock (and the async path a third, for the per-session lease). With
enough concurrent turns every pooled connection was held by a turn waiting
for another one: the pool deadlocked until its timeout and visitors got 500s.

These tests run the real API against a deliberately small QueuePool
(capacity 3), the same shape as production's 5 + 10 pool under 5x the load.
Disposable DB; fake model with latency (no provider calls).
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database
from app.core.config import settings as current_settings
from app.core.db_connection import has_uncommitted_writes, release_idle_connection
from app.models.public_chat import PublicChatConfiguration
from app.models.ticket import Ticket
from app.services import agent_workflow_service as workflow_module
from app.services import public_chat_service
from app.services import public_chat_turn_dispatcher as dispatcher_module
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from tests import test_public_chat as base
from tests import test_public_chat_async as chat

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated


class PoolProbe:
    def __init__(self, engine) -> None:
        self.pool = engine.sync_engine.pool
        self._checked_out: set[object] = set()
        self.peak = 0
        event.listen(self.pool, "checkout", self._checkout)
        event.listen(self.pool, "checkin", self._checkin)

    @property
    def out(self) -> int:
        return len(self._checked_out)

    def _checkout(self, _dbapi_connection, connection_record, _connection_proxy) -> None:
        self._checked_out.add(connection_record)
        self.peak = max(self.peak, self.out)

    def _checkin(self, _dbapi_connection, connection_record) -> None:
        # SQLAlchemy deliberately emits ``checkin`` after a failed/cancelled
        # checkout too. In that path no ``checkout`` event was emitted, so a
        # raw increment/decrement counter becomes negative even though nothing
        # is checked out. Track records that completed checkout instead.
        self._checked_out.discard(connection_record)


@pytest_asyncio.fixture
async def small_pool(monkeypatch):
    """Point the whole application at a 2 + 1 connection pool."""
    engine = create_async_engine(
        current_settings.database_url,
        pool_size=2,
        max_overflow=1,
        pool_timeout=8,
        pool_pre_ping=True,  # as the application engine
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", maker)
    monkeypatch.setattr(workflow_module, "engine", engine)
    monkeypatch.setattr(dispatcher_module, "AsyncSessionLocal", maker)
    # A fresh per-pool analysis bound (computed from this engine on first use).
    capacity_cls = getattr(
        __import__("app.core.db_connection", fromlist=["AnalysisCapacity"]),
        "AnalysisCapacity",
        None,
    )
    if capacity_cls is not None:
        monkeypatch.setattr(
            workflow_module,
            "analysis_capacity",
            capacity_cls(current_settings.agent_analysis_max_concurrency),
            raising=False,
        )
    probe = PoolProbe(engine)
    yield probe
    await turn_dispatcher.drain(timeout=60)
    await engine.dispose()


async def _tenant(db, client, org_scope, *, sessions: int, async_turns: bool):
    org = await org_scope()
    _cfg, key = await base._make_widget_config(
        db, org, max_messages_per_minute=60, async_turns_enabled=async_turns
    )
    tokens = [(await base._create_session(client, key))["session"]["token"] for _ in range(sessions)]
    return org.id, tokens


# ---------------------------------------------------------------------------
# release_idle_connection rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_transaction_is_released(db, org_scope):
    org = await org_scope()
    await db.execute(select(Ticket.id).where(Ticket.organization_id == org.id))
    assert db.in_transaction()
    assert await release_idle_connection(db) is True
    assert not db.in_transaction()


@pytest.mark.asyncio
async def test_pending_or_flushed_writes_are_never_committed_early(db, org_scope):
    org = await org_scope()
    _cfg, _key = await base._make_widget_config(db, org)
    cfg = (
        await db.execute(
            select(PublicChatConfiguration).where(PublicChatConfiguration.organization_id == org.id)
        )
    ).scalar_one()
    cfg.display_name = "pending change"
    assert has_uncommitted_writes(db)
    assert await release_idle_connection(db) is False
    await db.flush()  # flushed but uncommitted: still not releasable
    assert await release_idle_connection(db) is False
    await db.rollback()
    await db.refresh(cfg)
    assert cfg.display_name != "pending change"


@pytest.mark.asyncio
async def test_row_locks_and_xact_locks_are_never_released_early(db, org_scope):
    org = await org_scope()
    await db.execute(select(Ticket.id).where(Ticket.organization_id == org.id).with_for_update())
    assert await release_idle_connection(db) is False
    await db.rollback()
    await db.execute(text("SELECT pg_advisory_xact_lock(424242)"))
    assert await release_idle_connection(db) is False
    await db.rollback()


# ---------------------------------------------------------------------------
# Pool exhaustion (the Phase 1P.9 benchmark failure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_sync_turns_do_not_deadlock_a_small_pool(
    db, client, org_scope, monkeypatch, small_pool
):
    """Fails on Phase 1P.9 (hold-and-wait: pool timeouts -> 500s). Passes now."""
    chat._stub(monkeypatch, delay=0.3)
    monkeypatch.setattr(public_chat_service.settings, "public_chat_async_enabled", False)
    _org, tokens = await _tenant(db, client, org_scope, sessions=8, async_turns=False)
    small_pool.peak = 0
    responses = await asyncio.wait_for(
        asyncio.gather(*(chat._send(client, t, f"q{i}", delivery="sync") for i, t in enumerate(tokens))),
        timeout=60,
    )
    assert [r.status_code for r in responses] == [200] * 8, [r.text[:80] for r in responses]
    assert all(r.json()["reply"] for r in responses)
    assert small_pool.peak <= 3


@pytest.mark.asyncio
async def test_concurrent_async_turns_complete_on_a_small_pool(
    db, client, org_scope, monkeypatch, small_pool
):
    chat._stub(monkeypatch, delay=0.3)
    monkeypatch.setattr(public_chat_service.settings, "public_chat_async_enabled", True)
    _org, tokens = await _tenant(db, client, org_scope, sessions=6, async_turns=True)
    for i, token in enumerate(tokens):
        assert (await chat._send(client, token, f"q{i}")).json()["processing_state"] == "accepted"
    await turn_dispatcher.drain(timeout=60)
    for token in tokens:
        state = await chat._updates(client, token)
        assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]
    assert small_pool.peak <= 3


@pytest.mark.asyncio
async def test_cancelled_turn_returns_every_connection_and_lock(
    db, client, org_scope, monkeypatch, small_pool
):
    chat._stub(monkeypatch, delay=5.0)
    monkeypatch.setattr(public_chat_service.settings, "public_chat_async_enabled", False)
    _org, (token,) = await _tenant(db, client, org_scope, sessions=1, async_turns=False)
    task = asyncio.create_task(chat._send(client, token, "slow", delivery="sync"))
    await asyncio.sleep(0.8)  # inside the model call
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)
    assert small_pool.out == 0
    assert small_pool.pool.checkedout() == 0
    # No advisory lock leaked: a fresh turn on the same ticket proceeds.
    chat._stub(monkeypatch, delay=0.0)
    retry = await asyncio.wait_for(chat._send(client, token, "again", delivery="sync"), 20)
    assert retry.status_code == 200 and retry.json()["reply"]


@pytest.mark.asyncio
async def test_turn_survives_a_database_connection_timeout(
    db, client, org_scope, monkeypatch, small_pool
):
    """Every pooled connection is busy when the turn starts: the claim times
    out, the job stays pending, and the visitor's next poll recovers it."""
    chat._stub(monkeypatch)
    monkeypatch.setattr(public_chat_service.settings, "public_chat_async_enabled", True)
    monkeypatch.setattr(dispatcher_module, "PENDING_GRACE_SECONDS", 0)
    _org, (token,) = await _tenant(db, client, org_scope, sessions=1, async_turns=True)
    hog_engine = database.engine
    hogs = []
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        await chat._send(client, token, "during an outage")
    for _ in range(3):
        hogs.append(await hog_engine.connect())
    with monkeypatch.context() as scoped:
        scoped.setattr(hog_engine.sync_engine.pool, "_timeout", 0.5)
        turn_dispatcher.submit(
            (await chat._jobs(db, _org))[0].id, _org
        )
        await turn_dispatcher.drain(timeout=20)  # claim timed out, logged
    for conn in hogs:
        await conn.close()
    (job,) = await chat._jobs(db, _org)
    await db.refresh(job)
    assert job.status == "pending"
    await chat._updates(client, token)  # the visitor polls -> recovery
    await turn_dispatcher.drain(timeout=20)
    state = await chat._updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]
