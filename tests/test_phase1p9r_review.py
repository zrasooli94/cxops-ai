"""Phase 1P.9R independent review — regression tests for confirmed findings
and for invariants the hardening must not have weakened.

* Advisory locks: a dedicated lock connection (a chat turn's lease, or the
  analysis lock connection) releases EVERY session-level lock it holds before
  returning to the pool - including one granted to a lock call that was then
  cancelled (finding: the turn lease only unlocked its own key).
* Expired sessions: a queued turn for a session that expired before it ran
  completes without a model call or reply, and the visitor's token is refused.
* Authorization: the usage report still requires staff auth.
* Rate limits: the per-session burst limit is still enforced after the
  limiter rewrite.

Disposable DB; fake model (no provider calls).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import database
from app.core.config import settings as current_settings
from app.core.db_connection import dedicated_lock_connection
from app.models.public_chat import PublicChatSession
from app.services.delivery_lease import chat_turn_lease_connection
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from tests import test_public_chat as base
from tests import test_public_chat_async as chat

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated
_async_platform_on = chat._async_platform_on

PROBE_KEY = 0x7E57_10C4


# ---------------------------------------------------------------------------
# Advisory-lock hygiene
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pooled(monkeypatch):
    """A real one-connection QueuePool: a lock left on a connection that goes
    back to the pool stays held (with the suite's NullPool, closing a
    connection would end its server session and hide the leak)."""
    engine = create_async_engine(current_settings.database_url, pool_size=1, max_overflow=0)
    monkeypatch.setattr(database, "engine", engine)
    yield engine
    await engine.dispose()


async def _lock_is_free_outside(key: int) -> bool:
    outside = create_async_engine(current_settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with outside.connect() as conn:
            got = (
                await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
            ).scalar()
            if got:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            await conn.commit()
            return bool(got)
    finally:
        await outside.dispose()


@pytest.mark.asyncio
async def test_turn_lease_connection_releases_every_lock_it_carries(pooled):
    """Fails on the 1P.9R WIP commit (the lease unlocked only its own key, so
    the analysis lock taken on the same connection went back to the pool)."""
    async with chat_turn_lease_connection(9_000_001) as conn:
        assert conn is not None
        # The analysis lock is taken on the same connection by a chat turn.
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": PROBE_KEY})
        assert not await _lock_is_free_outside(PROBE_KEY)
    assert await _lock_is_free_outside(PROBE_KEY)


@pytest.mark.asyncio
async def test_turn_lease_connection_releases_locks_after_an_error(pooled):
    with contextlib.suppress(RuntimeError):
        async with chat_turn_lease_connection(9_000_002) as conn:
            assert conn is not None
            await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": PROBE_KEY})
            raise RuntimeError("turn failed")
    assert await _lock_is_free_outside(PROBE_KEY)


@pytest.mark.asyncio
async def test_cancelled_blocking_lock_call_never_leaks_a_lock(pooled):
    """Another holder owns the analysis lock; our analysis blocks on it and is
    cancelled while the holder releases. Whatever the race, the connection
    returned to the pool must not hold the lock."""
    holder_engine = create_async_engine(current_settings.database_url, pool_size=1, max_overflow=0)
    holder = await holder_engine.connect()
    await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": PROBE_KEY})

    async def _waiting_analysis() -> None:
        async with dedicated_lock_connection(pooled) as conn:
            await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": PROBE_KEY})
            await asyncio.sleep(30)

    task = asyncio.create_task(_waiting_analysis())
    await asyncio.sleep(0.3)  # blocked inside pg_advisory_lock
    task.cancel()
    await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PROBE_KEY})
    await holder.commit()
    await holder.close()
    await holder_engine.dispose()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert await _lock_is_free_outside(PROBE_KEY)


@pytest.mark.asyncio
async def test_dedicated_lock_connection_releases_locks_on_normal_exit(pooled):
    async with dedicated_lock_connection(pooled) as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": PROBE_KEY})
    assert await _lock_is_free_outside(PROBE_KEY)


# ---------------------------------------------------------------------------
# Expired sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_queued_for_a_session_that_expired_is_finished_safely(
    db, client, org_scope, monkeypatch
):
    fake = chat._stub(monkeypatch)
    org_id, token = await chat._setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        accepted = await chat._send(client, token, "Are you open today?")
    assert accepted.json()["processing_state"] == "accepted"
    await db.execute(
        update(PublicChatSession)
        .where(PublicChatSession.organization_id == org_id)
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db.commit()

    await turn_dispatcher.recover_on_startup()
    await turn_dispatcher.drain()
    (job,) = await chat._jobs(db, org_id)
    await db.refresh(job)
    assert job.status in {"completed", "failed"}
    assert fake.invocation_count == 0
    refused = await client.get(
        "/public/chat/sessions/updates?after_id=0", headers=base._origin_headers(token)
    )
    assert refused.status_code in (401, 409)


# ---------------------------------------------------------------------------
# Authorization and rate limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_report_requires_staff_authentication(client):
    response = await client.get("/observability/usage")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_per_session_rate_limit_is_still_enforced(db, client, org_scope, monkeypatch):
    chat._stub(monkeypatch)
    _org, token = await chat._setup(db, client, org_scope, max_messages_per_minute=2)
    statuses = [(await chat._send(client, token, f"m{i}")).status_code for i in range(4)]
    assert statuses[:2] == [200, 200]
    assert statuses[2:] == [429, 429]
    await turn_dispatcher.drain()
