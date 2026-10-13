"""Phase 1P.9R — no stranded turns without a visitor polling.

Phase 1P.9 recovered lost turns when the visitor polled and once at start-up
(at most 100, and never a turn whose claim was still fresh when the process
restarted). These tests cover what was left: a backlog larger than one page,
a crash mid-turn with the visitor gone, start-up recovery while the database
is unreachable, the hourly sweep, the scheduled GitHub worker racing the
in-process dispatcher, and cursor paging. Disposable DB; fake model.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app.models.integration_job import IntegrationJob
from app.services import public_chat_turn_dispatcher as dispatcher_module
from app.services.public_chat_service import PUBLIC_CHAT_TURN_JOB_TYPE
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from tests import test_public_chat as base
from tests import test_public_chat_async as chat

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated
_async_platform_on = chat._async_platform_on


async def _queue_without_dispatch(monkeypatch, client, tokens, text="queued"):
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        for i, token in enumerate(tokens):
            response = await chat._send(client, token, f"{text} {i}")
            assert response.json()["processing_state"] == "accepted"


async def _tenant_sessions(db, client, org_scope, count: int) -> tuple[int, list[str]]:
    org = await org_scope()
    _cfg, key = await base._make_widget_config(
        db, org, max_messages_per_minute=60, async_turns_enabled=True
    )
    tokens = [(await base._create_session(client, key))["session"]["token"] for _ in range(count)]
    return org.id, tokens


async def _replied(client, token) -> bool:
    state = await chat._updates(client, token)
    return any(m["direction"] == "outbound" for m in state["messages"])


@pytest.mark.asyncio
async def test_startup_recovers_a_backlog_larger_than_one_page(
    db, client, org_scope, monkeypatch
):
    """Phase 1P.9 resumed at most 100 turns at start-up; the rest waited for
    a visitor poll or the unreliable scheduler."""
    chat._stub(monkeypatch)
    monkeypatch.setattr(dispatcher_module, "RECOVERY_PAGE_SIZE", 25)
    tokens: list[str] = []
    for _ in range(3):  # 3 tenants x 40 sessions = 120 queued turns
        _org, tenant_tokens = await _tenant_sessions(db, client, org_scope, 40)
        tokens += tenant_tokens
    await _queue_without_dispatch(monkeypatch, client, tokens)

    scheduled = await turn_dispatcher.recover_all(page_size=25)
    assert scheduled >= 120
    await turn_dispatcher.drain(timeout=120)
    # Checked one by one (the test engine is unpooled; 120 parallel polls
    # would exceed the database's connection limit, not test the app).
    assert all([await _replied(client, t) for t in tokens])


@pytest.mark.asyncio
async def test_turn_interrupted_mid_call_completes_without_a_visitor(
    db, client, org_scope, monkeypatch
):
    """The process died while the turn was 'processing' (fresh claim), and the
    visitor closed the tab. Start-up recovery schedules the re-check for when
    the claim turns stale - no visitor poll, no periodic database polling."""
    chat._stub(monkeypatch)
    monkeypatch.setattr(dispatcher_module.settings, "public_chat_turn_stale_seconds", 2)
    org_id, (token,) = await _tenant_sessions(db, client, org_scope, 1)
    await _queue_without_dispatch(monkeypatch, client, [token])
    await db.execute(
        update(IntegrationJob)
        .where(
            IntegrationJob.organization_id == org_id,
            IntegrationJob.job_type == PUBLIC_CHAT_TURN_JOB_TYPE,
        )
        .values(status="processing", attempts=1, locked_at=datetime.now(UTC))
    )
    await db.commit()

    assert await turn_dispatcher.recover_on_startup() >= 1  # restart
    await turn_dispatcher.drain(timeout=30)  # nobody polls meanwhile
    (job,) = await chat._jobs(db, org_id)
    await db.refresh(job)
    assert job.status == "completed"
    assert await _replied(client, token)


@pytest.mark.asyncio
async def test_startup_recovery_retries_until_the_database_answers(
    db, client, org_scope, monkeypatch
):
    chat._stub(monkeypatch)
    _org, (token,) = await _tenant_sessions(db, client, org_scope, 1)
    await _queue_without_dispatch(monkeypatch, client, [token])

    real = turn_dispatcher.recover_all
    attempts = {"n": 0}

    async def _flaky(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("database is waking up")
        return await real(**kwargs)

    monkeypatch.setattr(turn_dispatcher, "recover_all", _flaky)
    monkeypatch.setattr(dispatcher_module, "STARTUP_RECOVERY_BACKOFF_SECONDS", (0, 0.05))
    monkeypatch.setattr(dispatcher_module.settings, "public_chat_turn_sweep_seconds", 0)
    await asyncio.wait_for(turn_dispatcher.run_maintenance(), 10)
    await turn_dispatcher.drain(timeout=30)
    assert attempts["n"] == 2
    assert await _replied(client, token)


@pytest.mark.asyncio
async def test_hourly_sweep_picks_up_turns_nobody_is_waiting_on(
    db, client, org_scope, monkeypatch
):
    chat._stub(monkeypatch)
    _org, (token,) = await _tenant_sessions(db, client, org_scope, 1)
    monkeypatch.setattr(dispatcher_module, "STARTUP_RECOVERY_BACKOFF_SECONDS", (0,))
    monkeypatch.setattr(dispatcher_module.settings, "public_chat_turn_sweep_seconds", 0.3)
    maintenance = asyncio.create_task(turn_dispatcher.run_maintenance())
    try:
        await asyncio.sleep(0.1)  # start-up recovery ran: nothing queued yet
        await _queue_without_dispatch(monkeypatch, client, [token])  # lost dispatch
        for _ in range(50):
            await asyncio.sleep(0.1)
            await turn_dispatcher.drain(timeout=5)
            if await _replied(client, token):
                break
    finally:
        maintenance.cancel()
    assert await _replied(client, token)


@pytest.mark.asyncio
async def test_scheduled_worker_racing_the_dispatcher_replies_once(
    db, client, org_scope, monkeypatch
):
    """The GitHub one-shot worker and the API's dispatcher both try the same
    turn; fenced claims and the reply de-duplication allow exactly one reply."""
    from scripts import worker

    fake = chat._stub(monkeypatch, delay=0.2)
    org_id, tokens = await _tenant_sessions(db, client, org_scope, 4)
    await _queue_without_dispatch(monkeypatch, client, tokens)
    await asyncio.gather(
        worker.run_worker_once(max_jobs=10, max_seconds=30),
        turn_dispatcher.recover_on_startup(),
    )
    await turn_dispatcher.drain(timeout=60)
    for token in tokens:
        state = await chat._updates(client, token)
        assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]
    assert fake.invocation_count == 4
    jobs = await chat._jobs(db, org_id)
    assert all(job.status == "completed" for job in jobs)


@pytest.mark.asyncio
async def test_cursor_pages_through_a_long_backlog_in_order(db, client, org_scope, monkeypatch):
    """A widget reconnecting after a long gap catches up page by page, in
    order, with no duplicates or gaps."""
    chat._stub(monkeypatch)
    _org, (token,) = await _tenant_sessions(db, client, org_scope, 1)
    for i in range(30):
        await chat._send(client, token, f"m{i:02d}", delivery="sync")
    seen: list[int] = []
    cursor = 0
    for _ in range(20):
        page = await chat._updates(client, token, after_id=cursor)
        ids = [m["id"] for m in page["messages"]]
        if not ids:
            break
        assert ids == sorted(ids) and ids[0] > cursor
        seen += ids
        cursor = page["cursor"]
    assert len(seen) == len(set(seen)) == 60
    bodies = [m["body"] for m in (await chat._updates(client, token))["messages"]]
    assert [b for b in bodies if b.startswith("m")][:3] == ["m00", "m01", "m02"]
