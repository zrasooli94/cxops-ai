"""Safe, offline negative tests for the real-worker disposable database gate."""
from __future__ import annotations

import pytest

from scripts.worker_disposable_guard import require_disposable_environment

URL = (
    "postgresql+asyncpg://cxops_probe:cxops_probe"
    "@127.0.0.1:5432/cxops_worker_probe_disposable"
)


def safe() -> dict[str, str]:
    return {
        "CXOPS_WORKER_DISPOSABLE_PROBE": "true",
        "GITHUB_ACTIONS": "true",
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "AUTH_MODE": "hs256",
        "AUTH_DEV_MODE": "false",
        "DATABASE_URL": URL,
    }


def test_exact_synthetic_local_target_passes() -> None:
    assert require_disposable_environment(safe()) == URL


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("CXOPS_WORKER_DISPOSABLE_PROBE", "True"),
        ("GITHUB_ACTIONS", ""),
        ("ENVIRONMENT", "production"),
        ("ENVIRONMENT", "staging"),
        ("DEBUG", "true"),
        ("AUTH_MODE", "jwks"),
        ("AUTH_DEV_MODE", "true"),
        ("DATABASE_URL", "postgresql+asyncpg://x:x@neon.tech/x"),
        ("DATABASE_URL", "postgresql+asyncpg://cxops_probe:cxops_probe@localhost:5432/cxops_worker_probe_disposable"),
        ("DATABASE_URL", "postgresql+asyncpg://cxops_probe:cxops_probe@127.0.0.1:5432/cxops"),
        ("DATABASE_URL", URL + "?sslmode=disable"),
        ("DATABASE_URL", URL.replace("cxops_probe:cxops_probe", "admin:password")),
        ("OPENAI_API_KEY", "sk-not-a-real-test-key"),
        ("ZENDESK_API_KEY", "synthetic"),
        ("AWS_ACCESS_KEY_ID", "synthetic"),
        ("ENCRYPTION_KEYS", "synthetic"),
        ("HANDOFF_NOTIFICATION_EMAIL_URL", "https://test.invalid"),
        ("CXOPS_STAGING_TICKET_ACTIONS_ENABLED", "false"),
        ("CXOPS_WORKER_CLOCK_EXECUTOR_ENABLED", "true"),
        ("NEON_DATABASE_URL", "synthetic"),
        ("RENDER_API_KEY", "synthetic"),
    ],
)
def test_unsafe_database_or_provider_environment_is_rejected(
    key: str, bad_value: str
) -> None:
    env = safe()
    env[key] = bad_value
    with pytest.raises(ValueError):
        require_disposable_environment(env)


def test_error_never_echoes_pasted_database_password() -> None:
    env = safe()
    env["DATABASE_URL"] = "postgresql+asyncpg://db:top-secret@public.example/db"
    with pytest.raises(ValueError) as info:
        require_disposable_environment(env)
    assert "top-secret" not in str(info.value)


def test_unset_environment_is_denied() -> None:
    with pytest.raises(ValueError):
        require_disposable_environment({})
