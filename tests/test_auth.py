# Authentication tests.
#
# Settings are read from a cached pydantic Settings object. To make tests
# deterministic regardless of collection order, every test that depends on
# auth configuration pins the relevant environment variables with monkeypatch
# and then rebuilds the cached settings via reset_settings_cache().

import base64
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from pydantic import ValidationError

from app.api.deps import CurrentPrincipal
from app.core.auth import (
    ExpiredTokenError,
    InvalidClaimsError,
    InvalidTokenError,
    _decode_and_validate_token,
    _validate_auth_mode,
    create_principal_from_payload,
)
from app.core.config import reset_settings_cache
from app.main import app

# Synthetic test fixtures — obviously non-secret deterministic values.
TEST_SECRET = "x" * 32
ALT_SECRET = "y" * 32
TEST_ISSUER = "test-issuer"
TEST_AUDIENCE = "test-audience"
DEV_MODE_SECRET = "dev-secret-not-for-production-use"


os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = TEST_SECRET
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = TEST_ISSUER
os.environ["AUTH_JWT_AUDIENCE"] = TEST_AUDIENCE
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"


class _LogRecorder:
    """Recording stand-in for a structlog bound logger.

    structlog caches module-level loggers after first use, so
    structlog.testing.capture_logs() cannot observe loggers already used by
    earlier tests. Patching the module logger with a recorder captures exactly
    the events our code emits.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _record(self, event: str, **kwargs) -> None:
        self.events.append({"event": event, **kwargs})

    def info(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)

    def warning(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)

    def error(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)


auth_probe_app = FastAPI(title="auth-probe")


@auth_probe_app.get("/probe")
async def auth_probe(principal: CurrentPrincipal):
    """Protected endpoint used to exercise authentication without a database."""
    return {"subject": principal.subject, "email": principal.email}


def _build_token(
    *,
    sub: str = "user-123",
    email: str = "test@example.com",
    iss: str = TEST_ISSUER,
    aud: str = TEST_AUDIENCE,
    secret: str = TEST_SECRET,
    algorithm: str = "HS256",
    exp: int | None = None,
    include_sub: bool = True,
) -> str:
    """Build a signed test JWT."""
    payload: dict = {}
    if include_sub:
        payload["sub"] = sub
    if email:
        payload["email"] = email
    if iss:
        payload["iss"] = iss
    if aud:
        payload["aud"] = aud
    payload["exp"] = (
        exp
        if exp is not None
        else int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    )
    return jwt.encode(payload, secret, algorithm=algorithm)


def _build_none_algorithm_token() -> str:
    """Build an unsigned alg=none JWT by hand (jose refuses to encode it)."""

    def _b64(obj) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = _b64({"alg": "none", "typ": "JWT"})
    payload = _b64(
        {
            "sub": "user-123",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        }
    )
    return f"{header}.{payload}."


def _configure(monkeypatch, **overrides) -> None:
    """Pin auth env vars and rebuild the cached settings deterministically."""
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": TEST_SECRET,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": TEST_ISSUER,
        "AUTH_JWT_AUDIENCE": TEST_AUDIENCE,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


class TestTokenValidation:
    """JWT token decoding and validation (unit level)."""

    def test_valid_token_decodes_successfully(self, monkeypatch):
        _configure(monkeypatch)
        token = _build_token()

        decoded = _decode_and_validate_token(token)

        assert decoded["sub"] == "user-123"
        assert decoded["email"] == "test@example.com"

    def test_expired_token_raises_expired_error(self, monkeypatch):
        _configure(monkeypatch)
        token = _build_token(
            exp=int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        )

        with pytest.raises(ExpiredTokenError):
            _decode_and_validate_token(token)

    def test_missing_sub_claim_raises_invalid_claims(self, monkeypatch):
        _configure(monkeypatch)
        token = _build_token(include_sub=False)

        with pytest.raises(InvalidClaimsError):
            _decode_and_validate_token(token)

    def test_empty_sub_claim_raises_invalid_claims(self, monkeypatch):
        _configure(monkeypatch)
        token = _build_token(sub="")

        with pytest.raises(InvalidClaimsError):
            _decode_and_validate_token(token)

    def test_invalid_signature_raises_invalid_token(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWT_SECRET=ALT_SECRET)
        token = _build_token(secret=TEST_SECRET)

        with pytest.raises(InvalidTokenError):
            _decode_and_validate_token(token)

    def test_malformed_token_raises_invalid_token(self, monkeypatch):
        _configure(monkeypatch)

        with pytest.raises(InvalidTokenError):
            _decode_and_validate_token("not-a-jwt")

        with pytest.raises(InvalidTokenError):
            _decode_and_validate_token("a.b.c")

    def test_unsupported_algorithm_rejected(self, monkeypatch):
        _configure(monkeypatch)
        token = _build_token(algorithm="HS512")

        with pytest.raises(InvalidTokenError):
            _decode_and_validate_token(token)

    def test_wrong_issuer_raises_invalid_claims(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWT_ISSUER=TEST_ISSUER)
        token = _build_token(iss="wrong-issuer", aud="")

        with pytest.raises(InvalidClaimsError):
            _decode_and_validate_token(token)

    def test_wrong_audience_raises_invalid_claims(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWT_AUDIENCE=TEST_AUDIENCE)
        token = _build_token(iss="", aud="wrong-audience")

        with pytest.raises(InvalidClaimsError):
            _decode_and_validate_token(token)

    def test_issuer_optional_when_not_configured(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWT_ISSUER="")
        token = _build_token(iss="", aud="")

        decoded = _decode_and_validate_token(token)

        assert decoded["sub"] == "user-123"

    def test_audience_optional_when_not_configured(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWT_AUDIENCE="", AUTH_JWT_ISSUER="")
        token = _build_token(iss="", aud="")

        decoded = _decode_and_validate_token(token)

        assert decoded["sub"] == "user-123"

    def test_signature_required_none_algorithm_rejected(self, monkeypatch):
        _configure(monkeypatch)

        with pytest.raises(InvalidTokenError):
            _decode_and_validate_token(_build_none_algorithm_token())


class TestPrincipalCreation:
    """AuthenticatedPrincipal creation from a validated payload."""

    def test_create_principal_with_all_fields(self):
        payload = {
            "sub": "user-123",
            "email": "test@example.com",
            "iss": "test-issuer",
        }
        principal = create_principal_from_payload(payload)

        assert principal.subject == "user-123"
        assert principal.email == "test@example.com"
        assert principal.issuer == "test-issuer"
        assert principal.auth_method == "jwt"

    def test_create_principal_minimal_fields(self):
        payload = {"sub": "user-123"}

        principal = create_principal_from_payload(payload)

        assert principal.subject == "user-123"
        assert principal.email is None
        assert principal.issuer is None

    def test_missing_sub_raises_error(self):
        with pytest.raises(InvalidClaimsError):
            create_principal_from_payload({"email": "test@example.com"})


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def probe():
    return AsyncClient(
        transport=ASGITransport(app=auth_probe_app),
        base_url="http://testserver",
    )


class TestAuthEndpoints:
    """Integration tests through the ASGI app."""

    @pytest.mark.asyncio
    async def test_health_endpoint_public(self, client):
        async with client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_root_endpoint_public(self, client):
        async with client:
            response = await client.get("/")
        assert response.status_code == 200
        assert "service" in response.json()

    @pytest.mark.asyncio
    async def test_metrics_endpoint_public(self, client):
        async with client:
            response = await client.get("/metrics")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_zendesk_oauth_login_requires_auth(self, client):
        async with client:
            response = await client.get("/auth/zendesk/login", follow_redirects=False)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_zendesk_oauth_callback_public(self, client):
        async with client:
            response = await client.get("/auth/zendesk/callback?code=test&state=test")
        # Reachable without a human JWT (the browser follows Zendesk's redirect),
        # but the missing/forged state is rejected with a controlled 4xx.
        assert response.status_code != 401
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_zendesk_webhook_endpoint_uses_hmac(self, client):
        async with client:
            response = await client.post(
                "/webhooks/zendesk/tickets/unknown-integration-id",
                json={"test": "data"},
                headers={
                    "x-zendesk-webhook-signature": "invalid",
                    "x-zendesk-webhook-signature-timestamp": "1234567890",
                    "x-zendesk-webhook-invocation-id": "test-123",
                },
            )
        # Unknown integration fails closed (404) before any signature check, so
        # the endpoint never reveals whether an integration exists.
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_probe_requires_auth(self, probe):
        async with probe:
            response = await probe.get("/probe")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_authorization_header(self, probe):
        async with probe:
            response = await probe.get("/probe")
        assert response.status_code == 401
        assert "Bearer" in response.headers.get("WWW-Authenticate", "")

    @pytest.mark.asyncio
    async def test_malformed_authorization_header(self, probe):
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": "NotBearer token"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_jwt_rejected(self, probe):
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": "Bearer not-a-jwt"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_subject_rejected(self, monkeypatch, probe):
        _configure(monkeypatch)
        token = _build_token(include_sub=False)
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unsupported_algorithm_rejected(self, monkeypatch, probe):
        _configure(monkeypatch)
        token = _build_token(algorithm="HS512")
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self, monkeypatch, probe):
        _configure(monkeypatch)
        token = _build_token(iss="wrong-issuer", aud="")
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected(self, monkeypatch, probe):
        _configure(monkeypatch)
        token = _build_token(iss="", aud="wrong-audience")
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, monkeypatch, probe):
        _configure(monkeypatch)
        token = _build_token(
            exp=int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        )
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_accepted(self, monkeypatch, probe):
        _configure(monkeypatch)
        token = _build_token(sub="test-user")
        async with probe:
            response = await probe.get(
                "/probe",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["subject"] == "test-user"
        assert response.json()["email"] == "test@example.com"


class TestDevModeSecurity:
    """Development auth safety controls."""

    def test_dev_mode_defaults_to_disabled(self):
        from app.core.config import Settings

        settings = Settings(_env_file=None)
        assert settings.auth_dev_mode is False

    def test_dev_mode_rejected_at_configuration_in_production(self, monkeypatch):
        with pytest.raises(ValidationError):
            _configure(
                monkeypatch,
                AUTH_DEV_MODE="True",
                ENVIRONMENT="production",
                AUTH_JWT_SECRET=TEST_SECRET,
            )

    def test_dev_mode_allowed_in_development(self, monkeypatch):
        _configure(monkeypatch, AUTH_DEV_MODE="True", AUTH_MODE="hs256")
        _validate_auth_mode()

    def test_dev_secret_used_only_in_dev_mode(self, monkeypatch):
        from app.core.auth import _get_jwt_secret

        _configure(monkeypatch, AUTH_DEV_MODE="True", AUTH_MODE="hs256")
        assert _get_jwt_secret() == DEV_MODE_SECRET

        _configure(monkeypatch, AUTH_DEV_MODE="False", AUTH_MODE="hs256")
        assert _get_jwt_secret() == TEST_SECRET


class TestRedactionInLogs:
    """No tokens or secrets reach auth log output."""

    @pytest.mark.asyncio
    async def test_token_not_in_log_output(self, monkeypatch, probe):
        _configure(monkeypatch)
        recorder = _LogRecorder()
        monkeypatch.setattr("app.core.auth.log", recorder)
        token = _build_token()

        async with probe:
            await probe.get(
                "/probe",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert recorder.events, "expected auth log output"
        for entry in recorder.events:
            rendered = str(entry)
            assert token not in rendered
            assert TEST_SECRET not in rendered

    @pytest.mark.asyncio
    async def test_invalid_token_not_in_log_output(self, monkeypatch, probe):
        _configure(monkeypatch)
        recorder = _LogRecorder()
        monkeypatch.setattr("app.core.auth.log", recorder)
        bad_token = "Bearer definitely-not-a-real-token"

        async with probe:
            await probe.get(
                "/probe",
                headers={"Authorization": bad_token},
            )

        assert recorder.events, "expected auth log output"
        for entry in recorder.events:
            rendered = str(entry)
            assert bad_token not in rendered
            assert TEST_SECRET not in rendered
