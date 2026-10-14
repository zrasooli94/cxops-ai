"""Phase 1U-A: optional notification settings can never stop a process.

A malformed ``HANDOFF_*`` value used to be a pydantic field-parsing error
(raised before any model validator), i.e. an outage of chat, SLA and job
processing caused by an optional feature. These tests pin the fail-closed
behaviour for Settings, API start-up, the scheduled worker's configuration
loading and the delivery gate, and prove zero outbound notifications.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings, parse_handoff_flag
from app.services import handoff_notification_service as notifications
from app.services.public_chat_turn_dispatcher import handoff_dispatcher
from tests import test_phase1u_notification_hardening as hard

REPO_ROOT = Path(__file__).resolve().parents[1]

_configure_auth = hard._configure_auth
_phase1p_namespace_isolated = hard._phase1p_namespace_isolated
client = hard.client
db = hard.db
org_scope = hard.org_scope
provider = hard.provider

SECRET_CANARY = "tgram://999999999:CANARYsecretCANARYsecretCANARYsecret/1"

MALFORMED = {
    "flag_maybe": {"HANDOFF_NOTIFICATIONS_ENABLED": "maybe"},
    "flag_2": {"HANDOFF_NOTIFICATIONS_ENABLED": "2"},
    "activated_not_a_date": {
        "HANDOFF_NOTIFICATIONS_ENABLED": "true",
        "HANDOFF_NOTIFICATION_ACTIVATED_AT": "not-a-date",
    },
    "cap_invalid": {
        "HANDOFF_NOTIFICATIONS_ENABLED": "true",
        "HANDOFF_NOTIFICATION_ACTIVATED_AT": "2026-01-01T00:00:00+00:00",
        "HANDOFF_NOTIFICATION_MAX_PER_HOUR": "invalid",
    },
    "cap_float": {
        "HANDOFF_NOTIFICATIONS_ENABLED": "true",
        "HANDOFF_NOTIFICATION_ACTIVATED_AT": "2026-01-01T00:00:00+00:00",
        "HANDOFF_NOTIFICATION_MAX_PER_HOUR": "6.5",
    },
    "everything_malformed": {
        "HANDOFF_NOTIFICATIONS_ENABLED": "maybe",
        "HANDOFF_NOTIFICATION_ACTIVATED_AT": "not-a-date",
        "HANDOFF_NOTIFICATION_MAX_PER_HOUR": "invalid",
    },
    # the same garbage while the feature is explicitly OFF
    "off_activated_not_a_date": {
        "HANDOFF_NOTIFICATIONS_ENABLED": "false",
        "HANDOFF_NOTIFICATION_ACTIVATED_AT": "not-a-date",
    },
    "off_cap_invalid": {
        "HANDOFF_NOTIFICATIONS_ENABLED": "false",
        "HANDOFF_NOTIFICATION_MAX_PER_HOUR": "invalid",
    },
    "off_everything_malformed": {
        "HANDOFF_NOTIFICATIONS_ENABLED": "no",
        "HANDOFF_NOTIFICATION_ACTIVATED_AT": "not-a-date",
        "HANDOFF_NOTIFICATION_MAX_PER_HOUR": "invalid",
    },
}


def _settings_from_env(monkeypatch, env: dict[str, str]) -> Settings:
    for key in list(os.environ):
        if key.startswith("HANDOFF_NOTIFICATION"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


@pytest.mark.parametrize("name", list(MALFORMED))
def test_malformed_settings_never_raise_and_stay_off(name, monkeypatch):
    cfg = _settings_from_env(monkeypatch, MALFORMED[name])  # must not raise
    assert cfg.handoff_notifications_enabled is False
    if name.startswith("off_"):
        # explicitly off: still OFF; a bounded reason is kept for the operator
        assert "unparseable" in cfg.handoff_notification_config_error
    else:
        assert cfg.handoff_notification_config_error
    reason = cfg.handoff_notification_config_error
    assert len(reason) < 200
    for value in ("not-a-date", "invalid", "maybe", "6.5"):
        assert value not in reason  # field names only, never the offending value


def test_reason_never_echoes_a_credential_bearing_value(monkeypatch):
    cfg = _settings_from_env(
        monkeypatch,
        {
            "HANDOFF_NOTIFICATIONS_ENABLED": "maybe",
            "HANDOFF_NOTIFICATION_TELEGRAM_URL": SECRET_CANARY,
        },
    )
    assert cfg.handoff_notifications_enabled is False
    assert "CANARY" not in cfg.handoff_notification_config_error
    assert "999999999" not in cfg.handoff_notification_config_error


def test_direct_construction_with_wrong_types_is_also_safe():
    cfg = Settings(
        handoff_notifications_enabled="perhaps",
        handoff_notification_activated_at="yesterday",
        handoff_notification_max_per_hour=[],
        handoff_notification_channels=object(),
    )
    assert cfg.handoff_notifications_enabled is False
    assert cfg.handoff_notification_config_error


def test_the_derived_error_field_cannot_be_injected_from_the_environment(monkeypatch):
    cfg = _settings_from_env(
        monkeypatch, {"HANDOFF_NOTIFICATION_CONFIG_ERROR": "attacker text"}
    )
    assert cfg.handoff_notification_config_error == ""


def test_valid_rollout_is_unchanged_by_the_sanitiser(monkeypatch):
    cfg = _settings_from_env(
        monkeypatch,
        {
            "HANDOFF_NOTIFICATIONS_ENABLED": " TRUE ",
            "HANDOFF_NOTIFICATION_ACTIVATED_AT": "2026-10-01T00:00:00+00:00",
            "HANDOFF_NOTIFICATION_MAX_PER_HOUR": "3",
            "HANDOFF_NOTIFICATION_CHANNELS": "email",
        },
    )
    assert cfg.handoff_notifications_enabled is True
    assert cfg.handoff_notification_max_per_hour == 3
    assert cfg.handoff_notification_config_error == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("TRUE", True), (" yes ", True), ("on", True), ("1", True),
     ("false", False), ("no", False), ("OFF", False), ("0", False),
     ("maybe", None), ("", None), ("  ", None), (None, None), ("2", None)],
)
def test_one_flag_vocabulary_for_settings_and_the_delivery_gate(raw, expected):
    assert parse_handoff_flag(raw) is expected


# --- processes: API import path and scheduled-worker configuration loading ---

_PROBE = (
    "import json, sys\n"
    "import app.main\n"
    "{extra}"
    "from app.core.config import settings\n"
    "print(json.dumps({{'enabled': settings.handoff_notifications_enabled,"
    " 'error': settings.handoff_notification_config_error,"
    " 'apprise_loaded': 'apprise' in sys.modules}}))\n"
)


def _run_probe(env_overrides: dict[str, str], extra: str = "") -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HANDOFF_NOTIFICATION")}
    env.update(env_overrides)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE.format(extra=extra)],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("name", list(MALFORMED))
def test_api_process_starts_and_loads_no_notification_machinery(name):
    result = _run_probe(MALFORMED[name])
    assert result["enabled"] is False
    assert result["apprise_loaded"] is False


@pytest.mark.parametrize("name", ["flag_maybe", "everything_malformed", "off_everything_malformed"])
def test_scheduled_worker_configuration_loads(name):
    result = _run_probe(MALFORMED[name], extra="import scripts.worker\n")
    assert result["enabled"] is False
    assert result["apprise_loaded"] is False


@pytest.mark.parametrize("name", ["flag_maybe", "activated_not_a_date", "everything_malformed"])
def test_production_preflight_does_not_crash_on_malformed_notification_settings(name):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HANDOFF_NOTIFICATION")}
    env.update(MALFORMED[name])
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "scripts/preflight_production_env.py"],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env, check=False,
    )
    # Exit 1 is expected (this is not a production environment); what must not
    # happen is a pydantic traceback caused by the notification variables.
    assert "Traceback" not in proc.stderr + proc.stdout
    assert "handoff_notification" not in (proc.stderr + proc.stdout).lower()


# --- API lifespan: logs the reason, starts nothing -------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["flag_maybe", "everything_malformed", "off_everything_malformed"])
async def test_lifespan_logs_the_bounded_reason_and_starts_no_notification_task(
    name, monkeypatch
):
    from app import main as main_module
    from app.core import schema_gate
    from app.services import provider_warmup

    cfg = _settings_from_env(monkeypatch, MALFORMED[name])

    async def ok(_engine):
        return None

    async def nothing():
        return None

    events: list[tuple[str, dict]] = []
    recorder = types.SimpleNamespace(
        info=lambda event, **kw: events.append((event, kw)),
        warning=lambda event, **kw: events.append((event, kw)),
        error=lambda event, **kw: events.append((event, kw)),
    )
    maintenance = {"n": 0}
    monkeypatch.setattr(main_module, "settings", cfg)
    monkeypatch.setattr(main_module, "log", recorder)
    monkeypatch.setattr(schema_gate, "check_schema", ok)
    monkeypatch.setattr(provider_warmup, "warm_provider_clients", nothing)
    monkeypatch.setattr(
        handoff_dispatcher, "run_maintenance",
        lambda: maintenance.__setitem__("n", maintenance["n"] + 1),
    )
    async with main_module.lifespan(main_module.app):
        pass
    reasons = [kw for ev, kw in events if ev == "handoff_notifications_disabled_by_config"]
    assert len(reasons) == 1 and "unparseable" in reasons[0]["reason"]
    assert maintenance["n"] == 0
    assert len(handoff_dispatcher._inflight) == 0


# --- delivery gate: explicit OFF suppresses, invalid/missing defers, never sends -


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "expected_outcome", "expected_payload"),
    [
        ("false", "completed", "suppressed:disabled"),
        ("no", "completed", "suppressed:disabled"),
        ("maybe", "deferred", None),
        ("2", "deferred", None),
        ("", "deferred", None),
    ],
)
async def test_gate_distinguishes_explicit_off_from_invalid_or_missing_and_sends_nothing(
    flag, expected_outcome, expected_payload, db, client, org_scope, monkeypatch, provider
):
    hard._enable(monkeypatch)
    org = await hard._rispu_org(db, org_scope)
    (sid,) = await hard._make_sessions(db, org, 1)
    await hard._handoff(sid, org.id)
    (job,) = await hard._jobs(org.id)

    monkeypatch.setenv("HANDOFF_NOTIFICATIONS_ENABLED", flag)
    assert await hard._run_job(job.id) == expected_outcome

    (after,) = await hard._jobs(org.id)
    assert provider.calls == []  # ZERO outbound notifications in every case
    assert after.payload.get("outcome") == expected_payload
    if expected_outcome == "deferred":
        assert after.attempts == 0 and after.status == "retry"


@pytest.mark.asyncio
async def test_valid_yes_flag_is_judged_with_the_settings_vocabulary(
    db, client, org_scope, monkeypatch, provider
):
    hard._enable(monkeypatch)
    org = await hard._rispu_org(db, org_scope)
    (sid,) = await hard._make_sessions(db, org, 1)
    await hard._handoff(sid, org.id)
    (job,) = await hard._jobs(org.id)
    monkeypatch.setenv("HANDOFF_NOTIFICATIONS_ENABLED", "yes")
    assert await hard._run_job(job.id) == "completed"
    assert provider.calls == ["email", "telegram"]


@pytest.mark.asyncio
async def test_a_malformed_rollout_stages_no_job_and_never_changes_the_handoff(
    db, client, org_scope, monkeypatch, provider
):
    cfg = _settings_from_env(monkeypatch, MALFORMED["activated_not_a_date"])
    for field in (
        "handoff_notifications_enabled", "handoff_notification_activated_at",
        "handoff_notification_config_error",
    ):
        monkeypatch.setattr(notifications.settings, field, getattr(cfg, field))
    org = await hard._rispu_org(db, org_scope)
    (sid,) = await hard._make_sessions(db, org, 1)
    await hard._handoff(sid, org.id)
    from app.core.database import AsyncSessionLocal
    from app.models.public_chat import PublicChatSession

    async with AsyncSessionLocal() as check:
        assert await check.scalar(
            select(PublicChatSession.status).where(PublicChatSession.id == sid)
        ) == "human_requested"
    assert await hard._jobs(org.id) == []
    assert provider.calls == []

# Follow-up review: whitespace and explicit null are unlike literal "" in
# Pydantic. They previously slipped through the before-model validator.
@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("handoff_notifications_enabled", False),
        ("handoff_notification_activated_at", None),
        ("handoff_notification_max_per_hour", 6),
    ],
)
@pytest.mark.parametrize("blank", ["   ", " \t "])
def test_whitespace_only_notification_environment_values_are_safe(
    field, expected, blank, monkeypatch
):
    cfg = _settings_from_env(monkeypatch, {field.upper(): blank})
    assert getattr(cfg, field) == expected
    assert cfg.handoff_notifications_enabled is False
    assert cfg.handoff_notification_config_error == ""


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("handoff_notifications_enabled", False),
        ("handoff_notification_activated_at", None),
        ("handoff_notification_max_per_hour", 6),
    ],
)
def test_explicit_none_notification_values_do_not_crash_settings(field, expected):
    cfg = Settings(**{field: None})
    assert getattr(cfg, field) == expected
    assert cfg.handoff_notifications_enabled is False


@pytest.mark.parametrize("blank", ["   ", None])
def test_blank_activation_with_enabled_notification_flag_fails_closed(blank):
    cfg = Settings(
        handoff_notifications_enabled=True,
        handoff_notification_activated_at=blank,
    )
    assert cfg.handoff_notifications_enabled is False
    assert cfg.handoff_notification_config_error == "activation requires an aware UTC timestamp"


def test_blank_hourly_cap_uses_default_without_disabling_otherwise_valid_rollout():
    cfg = Settings(
        handoff_notifications_enabled=True,
        handoff_notification_activated_at="2026-10-01T00:00:00+00:00",
        handoff_notification_max_per_hour=" \t ",
    )
    assert cfg.handoff_notifications_enabled is True
    assert cfg.handoff_notification_max_per_hour == 6
    assert cfg.handoff_notification_config_error == ""
