"""Phase 1P.9R — zero-downtime rollout gates.

* Async turns are OFF unless the platform switch AND the tenant flag are on
  AND the widget asks for async; every other combination keeps the original
  synchronous contract (old widget x new API, new widget x gated-off API).
* The kill switch stops new async acceptance; turns already queued finish.
* Code that needs a newer schema refuses to start (and never serves) instead
  of failing customer requests; a database AHEAD of the code (code rollback
  after an expand-only migration) is allowed.
* The expand migration is compatible with the previous release's inserts.

Real API + disposable PostgreSQL, fake model (no provider calls).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.models.public_chat import PublicChatConfiguration
from app.services import public_chat_service
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from tests import test_public_chat as base
from tests import test_public_chat_async as chat

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated


def _platform(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(public_chat_service.settings, "public_chat_async_enabled", enabled)


# ---------------------------------------------------------------------------
# Feature gate matrix
# ---------------------------------------------------------------------------


def test_async_turns_are_disabled_by_default():
    from app.core.config import Settings

    assert Settings(_env_file=None).public_chat_async_enabled is False
    assert PublicChatConfiguration.__table__.c.async_turns_enabled.server_default is not None


@pytest.mark.parametrize(
    ("platform", "tenant", "delivery", "expected"),
    [
        (False, False, "async", "completed"),  # merged + deployed, nothing activated
        (False, True, "async", "completed"),  # kill switch beats tenant opt-in
        (True, False, "async", "completed"),  # other tenants stay synchronous
        (True, True, "sync", "completed"),  # old widget on an activated tenant
        (True, True, None, "completed"),  # old widget that never sends `delivery`
        (True, True, "async", "accepted"),  # the one activated combination
    ],
)
@pytest.mark.asyncio
async def test_async_is_used_only_when_every_gate_is_open(
    db, client, org_scope, monkeypatch, platform, tenant, delivery, expected
):
    chat._stub(monkeypatch)
    _platform(monkeypatch, platform)
    org_id, token = await chat._setup(db, client, org_scope, async_turns_enabled=tenant)
    body = {"client_message_id": f"gate-{platform}-{tenant}-{delivery}", "text": "Hello"}
    if delivery is not None:
        body["delivery"] = delivery
    response = await client.post(
        "/public/chat/messages", json=body, headers=base._origin_headers(token)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["processing_state"] == expected
    await turn_dispatcher.drain()
    if expected == "completed":
        # The original contract: the reply is in the response, no turn job.
        assert payload["reply"]
        assert await chat._jobs(db, org_id) == []
    else:
        assert payload["reply"] is None
        assert len(await chat._jobs(db, org_id)) == 1


@pytest.mark.asyncio
async def test_kill_switch_stops_new_async_but_queued_turns_finish(
    db, client, org_scope, monkeypatch
):
    chat._stub(monkeypatch)
    _platform(monkeypatch, True)
    org_id, token = await chat._setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        queued = await chat._send(client, token, "queued before the switch")
    assert queued.json()["processing_state"] == "accepted"

    _platform(monkeypatch, False)  # operator flips the kill switch
    after = await chat._send(client, token, "sent after the switch")
    assert after.json()["processing_state"] == "completed"

    # The already-queued turn is still recovered and answered exactly once.
    assert await turn_dispatcher.recover_on_startup() >= 1
    await turn_dispatcher.drain()
    state = await chat._updates(client, token)
    outbound = [m for m in state["messages"] if m["direction"] == "outbound"]
    assert len(outbound) == 2
    assert state["turns"] == []
    del org_id


@pytest.mark.asyncio
async def test_new_widget_falls_back_cleanly_when_the_tenant_is_not_activated(
    db, client, org_scope, monkeypatch
):
    """New frontend x new backend with the tenant still gated off: the widget's
    async request is served synchronously and the updates endpoint still
    answers (so the widget's cursor polling works in both modes)."""
    chat._stub(monkeypatch)
    _platform(monkeypatch, True)
    _org, token = await chat._setup(db, client, org_scope, async_turns_enabled=False)
    sent = await chat._send(client, token, "Hello")
    assert sent.json()["processing_state"] == "completed"
    state = await chat._updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]
    assert state["poll_after_ms"] >= 3000  # nothing pending: slow poll


# ---------------------------------------------------------------------------
# Expand-only migration compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_previous_release_inserts_still_work_on_the_expanded_schema(db, org_scope):
    """The previous release does not know ``async_turns_enabled`` (or the
    allowance columns). Its INSERT omits them; the server default keeps every
    tenant disabled and NULL allowances fall back to platform defaults."""
    org = await org_scope()
    await db.execute(
        text(
            "INSERT INTO public_chat_configurations (organization_id, "
            "public_widget_key_hash, display_name, welcome_message, allowed_origins, "
            "enabled, max_message_length, max_messages_per_minute, session_ttl_hours, "
            "grounded_auto_reply_enabled, created_at, updated_at) VALUES (:org, :hash, "
            "'Old release', 'Hi', CAST('[]' AS JSONB), true, 4000, 20, 24, false, now(), now())"
        ),
        {"org": org.id, "hash": "f" * 64},
    )
    await db.commit()
    row = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org.id
            )
        )
    ).scalar_one()
    assert row.async_turns_enabled is False
    assert row.monthly_ai_turn_limit is None
    assert row.monthly_ai_budget_usd is None


# ---------------------------------------------------------------------------
# Start-up schema gate
# ---------------------------------------------------------------------------


def test_schema_classification():
    from app.core.schema_gate import classify

    heads = frozenset({"1p9b0001"})
    known = frozenset({"1p9b0001", "1p9a0001", "1p5a0001"})
    assert classify(frozenset({"1p9b0001"}), heads, known) == "at_head"
    assert classify(frozenset({"1p5a0001"}), heads, known) == "behind"
    assert classify(frozenset({"1p9a0001"}), heads, known) == "behind"
    assert classify(frozenset({"2a0a0001"}), heads, known) == "ahead"
    assert classify(frozenset(), heads, known) == "unversioned"


@pytest.mark.asyncio
async def test_schema_gate_passes_at_head():
    from app.core.database import engine
    from app.core.schema_gate import check_schema

    status = await check_schema(engine, enforce=True)
    assert status.verdict == "at_head"


@pytest.mark.parametrize(
    ("current", "refuses"),
    [
        (frozenset({"1p5a0001"}), True),  # production today: code merged first
        (frozenset({"1p9a0001"}), True),  # half-migrated
        (frozenset(), True),  # unversioned database
        (frozenset({"9zz00001"}), False),  # code rolled back after migrating
    ],
)
@pytest.mark.asyncio
async def test_schema_gate_refuses_a_database_behind_the_code(monkeypatch, current, refuses):
    from app.core import schema_gate
    from app.core.database import engine

    async def _fake_current(_engine):
        return current

    monkeypatch.setattr(schema_gate, "read_current", _fake_current)
    if refuses:
        with pytest.raises(schema_gate.SchemaBehindError):
            await schema_gate.check_schema(engine, enforce=True)
    else:
        assert (await schema_gate.check_schema(engine, enforce=True)).verdict == "ahead"
    # Outside production the gate only reports.
    await schema_gate.check_schema(engine, enforce=False)


@pytest.mark.asyncio
async def test_api_never_starts_serving_on_an_older_schema(monkeypatch):
    """The lifespan raises before the application yields, which makes uvicorn
    exit before binding its port: Render keeps the previous deploy live."""
    from app.core import schema_gate
    from app.main import app, lifespan

    async def _behind(_engine):
        return frozenset({"1p5a0001"})

    monkeypatch.setattr(schema_gate, "read_current", _behind)
    monkeypatch.setattr(schema_gate.settings, "environment", "production")
    started = False
    with pytest.raises(schema_gate.SchemaBehindError):
        async with lifespan(app):
            started = True
    assert started is False


def test_worker_refuses_to_consume_jobs_on_an_older_schema(monkeypatch):
    from app.core import schema_gate
    from scripts import worker

    async def _behind(_engine):
        return frozenset({"1p5a0001"})

    consumed = []
    monkeypatch.setattr(schema_gate, "read_current", _behind)
    monkeypatch.setattr(schema_gate.settings, "environment", "production")
    monkeypatch.setattr(worker, "run_worker_once", lambda **_k: consumed.append(1))
    with pytest.raises(schema_gate.SchemaBehindError):
        worker.main(["--once"])
    assert consumed == []
