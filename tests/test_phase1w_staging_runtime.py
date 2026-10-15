"""Phase 1W: staging must not start unsafe background tasks or unmigrated API."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main, core
from app.core import schema_gate


@pytest.mark.asyncio
async def test_staging_lifespan_refuses_to_create_any_background_tasks(monkeypatch):
    events = []

    async def schema_is_ready(_engine):
        events.append("schema_gate")
        return SimpleNamespace(verdict="at_head")

    def forbid_runtime_loop():
        raise AssertionError("Staging must never start background dispatchers")

    monkeypatch.setattr(schema_gate, "check_schema", schema_is_ready)
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            environment="staging",
            handoff_notification_config_error="",
            handoff_notifications_enabled=False,
        ),
    )
    # This is a module-local replacement; it does not mutate global asyncio.
    monkeypatch.setattr(
        main, "asyncio", SimpleNamespace(get_running_loop=forbid_runtime_loop)
    )

    async with main.lifespan(main.app):
        events.append("served")

    assert events == ["schema_gate", "served"]


@pytest.mark.asyncio
@pytest.mark.parametrize("database_revision", [frozenset(), frozenset({"older"}), frozenset({"unknown-newer"})])
async def test_staging_schema_gate_fails_for_any_non_exact_revision(monkeypatch, database_revision):
    monkeypatch.setattr(
        schema_gate, "settings", SimpleNamespace(environment="staging")
    )
    monkeypatch.setattr(
        schema_gate,
        "build_revisions",
        lambda: (frozenset({"current"}), frozenset({"current", "older"})),
    )

    async def read_current(_engine):
        return database_revision

    monkeypatch.setattr(schema_gate, "read_current_with_retry", read_current)
    with pytest.raises(schema_gate.SchemaBehindError):
        await schema_gate.check_schema(engine=None)


@pytest.mark.asyncio
async def test_staging_accepts_only_exact_schema_revision(monkeypatch):
    monkeypatch.setattr(
        schema_gate, "settings", SimpleNamespace(environment="staging")
    )
    monkeypatch.setattr(
        schema_gate,
        "build_revisions",
        lambda: (frozenset({"current"}), frozenset({"current", "older"})),
    )

    async def read_current(_engine):
        return frozenset({"current"})

    monkeypatch.setattr(schema_gate, "read_current_with_retry", read_current)
    status = await schema_gate.check_schema(engine=None)
    assert status.verdict == "at_head"


@pytest.mark.asyncio
async def test_production_rollback_ahead_semantics_remain_intact(monkeypatch):
    monkeypatch.setattr(
        schema_gate, "settings", SimpleNamespace(environment="production")
    )
    monkeypatch.setattr(
        schema_gate,
        "build_revisions",
        lambda: (frozenset({"current"}), frozenset({"current", "older"})),
    )

    async def read_current(_engine):
        return frozenset({"future"})

    monkeypatch.setattr(schema_gate, "read_current_with_retry", read_current)
    status = await schema_gate.check_schema(engine=None)
    assert status.verdict == "ahead"
