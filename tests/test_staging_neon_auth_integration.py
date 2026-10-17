"""Staging auth configuration and backend routing are isolated from production."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core import auth
from app.core.auth import get_current_principal
from app.core.config import Settings
from staging.neon_jwt import (
    STAGING_AUTH_ORIGIN,
    NeonStagingIdentityUnavailable,
    NeonStagingTokenError,
)

STAGING_DB = (
    "postgresql+asyncpg://cxops_staging:synthetic-not-real@"
    "ep-tiny-dawn-azixl9yk.c-3.ap-southeast-1.aws.neon.tech"
    "/cxops_staging?ssl=require"
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "staging",
        "auth_mode": "neon_staging",
        "debug": False,
        "auth_dev_mode": False,
        "database_url": STAGING_DB,
        "openai_api_key": "",
        "zendesk_client_secret": "",
        "zendesk_webhook_secret": "",
        "ticket_event_webhook_secret": "",
        "handoff_notifications_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_staging_configuration_accepts_only_correct_free_isolated_neon_database():
    settings = _settings()
    assert settings.auth_mode == "neon_staging"
    assert settings.environment == "staging"
    assert settings.database_url.endswith("/cxops_staging?ssl=require")


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://cxops_staging:password@localhost:5432/cxops_staging?ssl=require",
        "postgresql+asyncpg://cxops_staging:password@ep-autumn-band-azbfej90.c-3.ap-southeast-1.aws.neon.tech/cxops_staging?ssl=require",
        "postgresql+asyncpg://cxops_staging:password@ep-tiny-dawn-azixl9yk.c-3.ap-southeast-1.aws.neon.tech/cxops_prod?ssl=require",
        "postgresql+asyncpg://production:password@ep-tiny-dawn-azixl9yk.c-3.ap-southeast-1.aws.neon.tech/cxops_staging?ssl=require",
        "postgresql+asyncpg://cxops_staging:password@ep-tiny-dawn-azixl9yk.c-3.ap-southeast-1.aws.neon.tech/cxops_staging?ssl=disable",
    ],
)
def test_staging_rejects_production_local_or_plaintext_database_urls(database_url):
    with pytest.raises(ValidationError, match="Staging DATABASE_URL"):
        _settings(database_url=database_url)


@pytest.mark.parametrize("values", [
    {"environment": "production"},
    {"environment": "development"},
    {"auth_mode": "jwks"},
    {"auth_mode": "hs256"},
    {"auth_dev_mode": True},
    {"debug": True},
    {"openai_api_key": "synthetic-banned-live-provider"},
    {"zendesk_client_secret": "synthetic-banned-live-provider"},
    {"zendesk_webhook_secret": "synthetic-banned-live-provider"},
    {"ticket_event_webhook_secret": "synthetic-banned-live-provider"},
    {"handoff_notifications_enabled": True},
])
def test_staging_rejects_wrong_auth_mode_dev_bypass_or_live_provider_secrets(values):
    with pytest.raises(ValidationError):
        _settings(**values)


@pytest.mark.asyncio
async def test_staging_api_accepts_only_verified_neon_identity(monkeypatch):

    config = SimpleNamespace(
        environment="staging", auth_mode="neon_staging", auth_dev_mode=False,
    )
    monkeypatch.setattr(auth, "get_settings", lambda: config)
    observed = []

    async def fake_verify(token: str):
        observed.append(token)
        assert token == "synthetic-staging-token"
        return {
            "iss": STAGING_AUTH_ORIGIN,
            "sub": "synthetic-staging-sub",
            "id": "synthetic-staging-sub",
            "email": "synthetic@example.test",
        }

    monkeypatch.setattr(auth, "verify_neon_staging_token", fake_verify)
    principal = await get_current_principal("Bearer synthetic-staging-token")
    assert principal.subject == "synthetic-staging-sub"
    assert principal.auth_method == "neon_staging_ed25519"
    assert observed == ["synthetic-staging-token"]


@pytest.mark.asyncio
async def test_staging_authentication_never_logs_a_real_agent_email(monkeypatch):
    config = SimpleNamespace(
        environment="staging", auth_mode="neon_staging", auth_dev_mode=False,
    )
    monkeypatch.setattr(auth, "get_settings", lambda: config)
    seen = []

    async def verified_staging_agent(_token):
        return {
            "sub": "staging-synthetic-user-id",
            "email": "private.agent@gmail.com",
        }

    monkeypatch.setattr(auth, "verify_neon_staging_token", verified_staging_agent)
    monkeypatch.setattr(auth.log, "info", lambda event, **values: seen.append((event, values)))
    principal = await get_current_principal("Bearer synthetic")
    assert principal.email == "private.agent@gmail.com"
    assert seen and seen[-1][0] == "auth_success"
    assert seen[-1][1]["email"] is None
    assert "private.agent@gmail.com" not in repr(seen)


@pytest.mark.asyncio
async def test_staging_rejects_bad_tokens_and_unreachable_identity(monkeypatch):

    monkeypatch.setattr(
        auth, "get_settings",
        lambda: SimpleNamespace(
            environment="staging", auth_mode="neon_staging", auth_dev_mode=False
        ),
    )

    async def reject(_token):
        raise NeonStagingTokenError("bad synthetic signature")

    monkeypatch.setattr(auth, "verify_neon_staging_token", reject)
    with pytest.raises(HTTPException) as exc:
        await get_current_principal("Bearer tampered")
    assert exc.value.status_code == 401
    assert "signature" not in str(exc.value.detail).lower()

    async def unavailable(_token):
        raise NeonStagingIdentityUnavailable("fake upstream unavailable")

    monkeypatch.setattr(auth, "verify_neon_staging_token", unavailable)
    with pytest.raises(HTTPException) as exc:
        await get_current_principal("Bearer synthetic")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_production_nhost_code_does_not_call_staging_verifier(monkeypatch):

    monkeypatch.setattr(
        auth, "get_settings",
        lambda: SimpleNamespace(
            environment="production", auth_mode="jwks",
            auth_dev_mode=False, auth_jwks_url="https://prod.example.test/jwks",
        ),
    )
    async def forbid_staging(_token):
        raise AssertionError("Neon must never be used on Nhost production")
    async def production_verify(_token):
        return {"sub": "nhost-production-synthetic", "iss": "https://prod.example.test"}
    monkeypatch.setattr(auth, "verify_neon_staging_token", forbid_staging)
    monkeypatch.setattr(auth, "verify_jwks_token", production_verify)
    principal = await get_current_principal("Bearer synthetic-jwks-token")
    assert principal.subject == "nhost-production-synthetic"
    assert principal.auth_method == "jwks"
