"""Phase 1C.3A Zendesk tenant-isolation tests.

Two organizations, two organization-owned Zendesk connections. Covers:
A  Org A /zendesk/me uses only credential A
B  Org A cannot cause credential B to be selected
C  Org A Zendesk ticket APIs never use connection B
D  OAuth initiation binds state to Org A
E  OAuth callback stores token under Org A only
F  callback state cannot be replayed
G  forged/expired state rejected
H  user cannot bind OAuth connection to an organization without membership
I  webhook for integration A resolves Org A
J  webhook for integration B resolves Org B
K  unknown integration webhook fails closed
L  same external ticket id safely in Org A and Org B
M  same customer email safely in Org A and Org B
N  internal Zendesk sync cannot cross tenant
O  no Zendesk token in logs / API responses
P  stale webhook rejected
Q  valid fresh webhook accepted
+ duplicate invocation dedup, unconfigured coverage, missing-secret fail-closed.
"""

import base64
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-zendesk-tenant-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-zendesk-tenant-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"
# Valid Fernet key so the encrypted columns can round-trip in tests.
os.environ["ENCRYPTION_KEYS"] = base64.urlsafe_b64encode(b"0" * 32).decode()
os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "false"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.integrations.zendesk.security import verify_zendesk_signature
from app.main import app
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket
from app.models.zendesk_oauth_state import ZendeskOAuthState
from app.models.zendesk_oauth_token import ZendeskOAuthToken

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-zendesk-tenant-issuer"
TEST_AUDIENCE = "test-zendesk-tenant-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"

X_TENANT = "X-CXOps-Organization-ID"

STATE_COOKIE = "zendesk_oauth_state"


def _configure(monkeypatch, **overrides) -> None:
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": TEST_SECRET,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": TEST_ISSUER,
        "AUTH_JWT_AUDIENCE": TEST_AUDIENCE,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
        "ZENDESK_SUBDOMAIN": "cxops-test",
        "ZENDESK_CLIENT_ID": "test-client",
        "ZENDESK_CLIENT_SECRET": "test-client-secret",
        "ZENDESK_REDIRECT_URI": "http://testserver/auth/zendesk/callback",
        "ZENDESK_OAUTH_SCOPE": "read write",
        "ZENDESK_OAUTH_STATE_TTL_SECONDS": "600",
        "ZENDESK_WEBHOOK_REPLAY_WINDOW_SECONDS": "300",
        "ENCRYPTION_KEYS": base64.urlsafe_b64encode(b"0" * 32).decode(),
        "ENCRYPTION_ALLOW_LEGACY_PLAINTEXT": "false",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _configure_env(monkeypatch):
    _configure(monkeypatch)
    yield


def _build_token(*, sub: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": sub,
            "email": f"{sub}@example.com",
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "exp": now + 3600,
            "iat": now,
        },
        TEST_SECRET,
        algorithm="HS256",
    )


def _h_alpha():
    return {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}


def _h_beta():
    return {"Authorization": f"Bearer {_build_token(sub=USER_BETA)}"}


@pytest.fixture
def make_client():
    """Factory that returns a fresh AsyncClient per call."""

    def _make():
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        )

    return _make


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def zendesk_env(db):
    """Org A + Org B, disjoint memberships, and two org-owned connections."""
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.subject.in_([USER_ALPHA, USER_BETA])
        )
    )
    await db.commit()

    org_a = Organization(name=f"org-a-{uuid.uuid4().hex[:8]}")
    org_b = Organization(name=f"org-b-{uuid.uuid4().hex[:8]}")
    db.add_all([org_a, org_b])
    await db.commit()
    await db.refresh(org_a)
    await db.refresh(org_b)
    org_a_id = org_a.id
    org_b_id = org_b.id

    token_a = ZendeskOAuthToken(
        organization_id=org_a_id,
        integration_id=f"int-a-{uuid.uuid4().hex[:16]}",
        access_token=f"tok-a-{uuid.uuid4().hex}",
        refresh_token=f"refresh-a-{uuid.uuid4().hex}",
        token_type="bearer",
        scope="read write",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
        refresh_token_expires_at=None,
        webhook_secret=f"wh-a-{uuid.uuid4().hex[:24]}",
    )
    token_b = ZendeskOAuthToken(
        organization_id=org_b_id,
        integration_id=f"int-b-{uuid.uuid4().hex[:16]}",
        access_token=f"tok-b-{uuid.uuid4().hex}",
        refresh_token=f"refresh-b-{uuid.uuid4().hex}",
        token_type="bearer",
        scope="read write",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
        refresh_token_expires_at=None,
        webhook_secret=f"wh-b-{uuid.uuid4().hex[:24]}",
    )

    db.add_all(
        [
            OrganizationMembership(
                subject=USER_ALPHA,
                organization_id=org_a_id,
                role=OrganizationRole.OWNER,
            ),
            OrganizationMembership(
                subject=USER_BETA,
                organization_id=org_b_id,
                role=OrganizationRole.OWNER,
            ),
            token_a,
            token_b,
        ]
    )
    await db.commit()
    await db.refresh(token_a)
    await db.refresh(token_b)

    env = {
        "org_a": org_a,
        "org_b": org_b,
        "org_a_id": org_a_id,
        "org_b_id": org_b_id,
        "token_a": token_a,
        "token_b": token_b,
        "integration_a": token_a.integration_id,
        "integration_b": token_b.integration_id,
        "secret_a": token_a.webhook_secret,
        "secret_b": token_b.webhook_secret,
    }

    try:
        yield env
    finally:
        await db.rollback()
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.subject.in_([USER_ALPHA, USER_BETA])
            )
        )
        await db.execute(
            delete(ZendeskOAuthToken).where(
                ZendeskOAuthToken.organization_id.in_([org_a_id, org_b_id])
            )
        )
        await db.execute(
            delete(Organization).where(Organization.id.in_([org_a_id, org_b_id]))
        )
        await db.commit()


def _sign(secret, payload: dict, *, when=None) -> dict:
    import base64
    import hashlib
    import hmac
    import json

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = (when or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + body,
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    return {
        "content": body,
        "timestamp": timestamp,
        "signature": signature,
    }


def _zendesk_payload(external_id):
    return {
        "type": "zen:event-type:ticket.created",
        "detail": {"id": external_id},
        "channel": "email",
    }


async def _request_spy(monkeypatch, zendesk_client, captured):
    async def fake_request(
        db, method, path, *, organization_id, retry_on_unauthorized=True, **kwargs
    ):
        captured.append(
            {
                "method": method,
                "path": path,
                "organization_id": organization_id,
            }
        )
        if path.startswith("/users/me"):
            return {"user": {"id": 1, "email": "a@example.com", "name": "A User"}}
        if path.startswith("/tickets/"):
            return {"ticket": {"id": 77, "subject": "S", "status": "open"}}
        return {"ok": True}

    monkeypatch.setattr(zendesk_client, "request", fake_request)


# ===================================================================
# A. Org A /zendesk/me uses only credential A
# ===================================================================
@pytest.mark.asyncio
async def test_org_a_me_uses_only_credential_a(
    monkeypatch, make_client, db, zendesk_env
):
    from app.integrations.zendesk.client import zendesk_client

    captured = []
    await _request_spy(monkeypatch, zendesk_client, captured)

    async with make_client() as client:
        r = await client.get("/zendesk/me", headers=_h_alpha())

    assert r.status_code == 200
    assert captured, "client request was not made"
    assert [c["organization_id"] for c in captured] == [zendesk_env["org_a_id"]]
    assert captured[0]["path"] == "/users/me.json"
    # The response never carries credential material.
    assert "token" not in r.text.lower()


@pytest.mark.asyncio
async def test_get_valid_token_is_org_scoped(db, zendesk_env):
    from app.services.zendesk_oauth_service import (
        ZendeskNotConfiguredError,
        ZendeskOAuthService,
    )

    token_a = await ZendeskOAuthService.get_valid_token(db, zendesk_env["org_a_id"])
    token_b = await ZendeskOAuthService.get_valid_token(db, zendesk_env["org_b_id"])

    assert token_a.access_token == zendesk_env["token_a"].access_token
    assert token_b.access_token == zendesk_env["token_b"].access_token
    assert token_a.id != token_b.id

    # An org with no connection fails closed.
    orphan = Organization(name=f"orphan-{uuid.uuid4().hex[:8]}")
    db.add(orphan)
    await db.commit()
    orphan_id = orphan.id
    try:
        with pytest.raises(ZendeskNotConfiguredError):
            await ZendeskOAuthService.get_valid_token(db, orphan.id)
    finally:
        await db.rollback()
        await db.execute(delete(Organization).where(Organization.id == orphan_id))
        await db.commit()


# ===================================================================
# B + H. selector cannot select credential B / non-member org binding
# ===================================================================
@pytest.mark.asyncio
async def test_org_a_cannot_select_credential_b(monkeypatch, make_client, zendesk_env):
    from app.integrations.zendesk.client import zendesk_client

    captured = []
    await _request_spy(monkeypatch, zendesk_client, captured)

    async with make_client() as client:
        r = await client.get(
            "/zendesk/me",
            headers={**_h_alpha(), X_TENANT: str(zendesk_env["org_b_id"])},
        )

    # Non-member selector is rejected before any credential resolution.
    assert r.status_code == 403
    assert captured == []


@pytest.mark.asyncio
async def test_oauth_login_without_membership_403(make_client, zendesk_env):
    async with make_client() as client:
        r = await client.get(
            "/auth/zendesk/login",
            headers={**_h_alpha(), X_TENANT: str(zendesk_env["org_b_id"])},
            follow_redirects=False,
        )
    assert r.status_code == 403


# ===================================================================
# C. Org A Zendesk ticket APIs never use connection B
# ===================================================================
@pytest.mark.asyncio
async def test_org_a_ticket_api_uses_only_credential_a(
    monkeypatch, make_client, zendesk_env
):
    from app.integrations.zendesk.client import zendesk_client

    captured = []
    await _request_spy(monkeypatch, zendesk_client, captured)

    async with make_client() as client:
        r = await client.get("/zendesk/tickets/1234", headers=_h_alpha())

    assert r.status_code == 200
    assert [c["organization_id"] for c in captured] == [zendesk_env["org_a_id"]]
    assert captured[0]["path"] == "/tickets/1234.json"


# ===================================================================
# D. OAuth initiation binds state to Org A
# ===================================================================
@pytest.mark.asyncio
async def test_oauth_initiation_binds_state_to_org_a(
    monkeypatch, make_client, db, zendesk_env
):
    async with make_client() as client:
        r = await client.get(
            "/auth/zendesk/login", headers=_h_alpha(), follow_redirects=False
        )

    assert r.status_code == 302
    assert "zendesk.com/oauth/authorizations/new" in r.headers["location"]

    state = client.cookies.get(STATE_COOKIE)
    assert state and len(state) >= 32

    result = await db.execute(
        select(ZendeskOAuthState).where(ZendeskOAuthState.state == state)
    )
    persisted = result.scalar_one_or_none()
    assert persisted is not None
    assert persisted.organization_id == zendesk_env["org_a_id"]
    assert persisted.subject == USER_ALPHA
    assert persisted.consumed_at is None

    # No organization or credential material is stamped into the URL.
    location = r.headers["location"]
    assert "organization" not in location.lower()
    assert "token" not in location.lower()
    assert str(zendesk_env["org_b_id"]) not in location


# ===================================================================
# E + F. callback stores token under Org A only; no replay
# ===================================================================
async def _run_callback(
    make_client,
    *,
    state,
    code="auth-code",
    cookie=STATE_COOKIE,
    cookie_value=None,
):
    async with make_client() as client:
        return await client.get(
            "/auth/zendesk/callback",
            params={"code": code, "state": state},
            cookies={cookie: cookie_value if cookie_value is not None else state},
        )


@pytest.mark.asyncio
async def test_callback_stores_token_under_org_a_only(
    monkeypatch, make_client, db, zendesk_env
):
    from app.repositories.zendesk_oauth_token_repository import (
        ZendeskOAuthTokenRepository,
    )
    from app.services import zendesk_oauth_service

    exchanged = []

    async def fake_exchange(db, *, code, organization_id, subject):
        exchanged.append(
            {
                "code": code,
                "organization_id": organization_id,
                "subject": subject,
            }
        )
        async with AsyncSessionLocal() as session:
            return await ZendeskOAuthTokenRepository.save_for_organization(
                session,
                organization_id=organization_id,
                access_token=f"issued-tok-{uuid.uuid4().hex}",
                refresh_token=f"issued-ref-{uuid.uuid4().hex}",
                token_type="bearer",
                scope="read write",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
                refresh_token_expires_at=None,
                connected_by=subject,
                integration_id=f"cb-{uuid.uuid4().hex[:16]}",
            )

    monkeypatch.setattr(
        zendesk_oauth_service.ZendeskOAuthService,
        "exchange_code",
        fake_exchange,
    )

    async with make_client() as client:
        r = await client.get(
            "/auth/zendesk/login", headers=_h_alpha(), follow_redirects=False
        )
        state = client.cookies.get(STATE_COOKIE)

    r = await _run_callback(make_client, state=state)

    assert r.status_code == 200
    body = r.json()
    assert body["organization_id"] == zendesk_env["org_a_id"]

    assert exchanged == [
        {
            "code": "auth-code",
            "organization_id": zendesk_env["org_a_id"],
            "subject": USER_ALPHA,
        }
    ]

    # Token persisted under Org A only.
    async with AsyncSessionLocal() as check:
        a_row = await ZendeskOAuthTokenRepository.get_for_organization(
            check, zendesk_env["org_a_id"]
        )
        b_row = await ZendeskOAuthTokenRepository.get_for_organization(
            check, zendesk_env["org_b_id"]
        )

    assert a_row is not None and a_row.access_token.startswith("issued-tok-")
    assert b_row is not None and b_row.access_token.startswith("tok-b-")

    # No token material is ever echoed to the browser (O).
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert "token" not in r.text.lower()


@pytest.mark.asyncio
async def test_callback_state_cannot_be_replayed(monkeypatch, make_client, zendesk_env):
    from app.services import zendesk_oauth_service

    calls = []

    async def fake_exchange(db, *, code, organization_id, subject):
        calls.append(organization_id)
        return zendesk_env["token_a"]

    monkeypatch.setattr(
        zendesk_oauth_service.ZendeskOAuthService,
        "exchange_code",
        fake_exchange,
    )

    async with make_client() as client:
        await client.get(
            "/auth/zendesk/login", headers=_h_alpha(), follow_redirects=False
        )
        state = client.cookies.get(STATE_COOKIE)

    first = await _run_callback(make_client, state=state)
    assert first.status_code == 200

    replayed = await _run_callback(make_client, state=state)
    assert replayed.status_code == 400
    # The durable state was already consumed; exchange never runs again.
    assert len(calls) == 1


# ===================================================================
# G. forged / expired state rejected
# ===================================================================
@pytest.mark.asyncio
async def test_forged_state_cookie_mismatch_rejected(make_client, zendesk_env):
    # URL state does not match the browser cookie → CSRF/cross-device binding
    # fails before any durable-state work.
    r = await _run_callback(
        make_client,
        state="url-state-mismatch",
        cookie_value="different-cookie-value",
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_expired_state_rejected(monkeypatch, make_client, db, zendesk_env):
    from app.repositories.zendesk_oauth_state_repository import (
        ZendeskOAuthStateRepository,
    )
    from app.services import zendesk_oauth_service

    calls = []

    async def fake_exchange(db, *, code, organization_id, subject):
        calls.append(organization_id)
        return zendesk_env["token_a"]

    monkeypatch.setattr(
        zendesk_oauth_service.ZendeskOAuthService,
        "exchange_code",
        fake_exchange,
    )

    expired_state = f"expired-{uuid.uuid4().hex[:16]}"
    await ZendeskOAuthStateRepository.create(
        db,
        state=expired_state,
        organization_id=zendesk_env["org_a_id"],
        subject=USER_ALPHA,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )

    r = await _run_callback(make_client, state=expired_state)
    assert r.status_code == 400
    assert calls == []


# ===================================================================
# I + J + K + Q. webhook trusted identity → organization resolution
# ===================================================================
@pytest.mark.asyncio
async def test_webhook_integration_a_resolves_org_a(make_client, zendesk_env):
    from app.models.integration_job import IntegrationJob

    external_id = 900_111_001
    signed = _sign(zendesk_env["secret_a"], _zendesk_payload(external_id))

    async with make_client() as client:
        r = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": f"inv-a-{uuid.uuid4().hex}",
            },
        )

    assert r.status_code == 202
    body = r.json()
    assert body["duplicate"] is False

    async with AsyncSessionLocal() as session:
        job = await session.get(IntegrationJob, body["job_id"])
        assert job is not None
        assert job.organization_id == zendesk_env["org_a_id"]


@pytest.mark.asyncio
async def test_webhook_integration_b_resolves_org_b(make_client, zendesk_env):
    from app.models.integration_job import IntegrationJob

    external_id = 900_222_002
    signed = _sign(zendesk_env["secret_b"], _zendesk_payload(external_id))

    async with make_client() as client:
        r = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_b']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": f"inv-b-{uuid.uuid4().hex}",
            },
        )

    assert r.status_code == 202

    async with AsyncSessionLocal() as session:
        job = await session.get(IntegrationJob, r.json()["job_id"])
        assert job is not None
        assert job.organization_id == zendesk_env["org_b_id"]


@pytest.mark.asyncio
async def test_unknown_integration_fails_closed(make_client, zendesk_env):
    signed = _sign("attacker-secret", _zendesk_payload(1))

    async with make_client() as client:
        r = await client.post(
            "/webhooks/zendesk/tickets/unknown-integration-xyz",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": "inv-xyz",
            },
        )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_invocation_is_deduped(make_client, zendesk_env):
    from app.models.integration_job import IntegrationJob

    external_id = 900_333_003
    signed = _sign(zendesk_env["secret_a"], _zendesk_payload(external_id))
    invocation = f"inv-dup-{uuid.uuid4().hex}"

    async with make_client() as client:
        r1 = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": invocation,
            },
        )
        r2 = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": invocation,
            },
        )

    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r2.json()["duplicate"] is True
    assert r1.json()["job_id"] == r2.json()["job_id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IntegrationJob).where(IntegrationJob.dedupe_key == invocation)
        )
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.organization_id == zendesk_env["org_a_id"]


# ===================================================================
# P. replay-window protections (stale / future / invalid)
# ===================================================================
@pytest.mark.asyncio
async def test_stale_webhook_rejected(make_client, zendesk_env):
    stale = datetime.now(timezone.utc) - timedelta(seconds=400)
    signed = _sign(zendesk_env["secret_a"], _zendesk_payload(1), when=stale)

    async with make_client() as client:
        r = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": f"inv-stale-{uuid.uuid4().hex}",
            },
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_future_webhook_rejected(make_client, zendesk_env):
    future = datetime.now(timezone.utc) + timedelta(seconds=400)
    signed = _sign(zendesk_env["secret_a"], _zendesk_payload(1), when=future)

    async with make_client() as client:
        r = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": f"inv-future-{uuid.uuid4().hex}",
            },
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_invalid_signature_rejected(make_client, zendesk_env):
    signed = _sign("wrong-secret", _zendesk_payload(1))

    async with make_client() as client:
        r = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": f"inv-bad-{uuid.uuid4().hex}",
            },
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_connection_without_webhook_secret_fails_closed(
    make_client, db, zendesk_env
):
    orphan = Organization(name=f"no-secret-{uuid.uuid4().hex[:8]}")
    db.add(orphan)
    await db.commit()
    no_secret_conn = ZendeskOAuthToken(
        organization_id=orphan.id,
        integration_id=f"int-ns-{uuid.uuid4().hex[:16]}",
        access_token="tok-ns",
        refresh_token=None,
        token_type="bearer",
        scope="read write",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
        webhook_secret=None,
    )
    db.add(no_secret_conn)
    await db.commit()
    integration_ns = no_secret_conn.integration_id

    signed = _sign("any-secret", _zendesk_payload(1))
    try:
        async with make_client() as client:
            r = await client.post(
                f"/webhooks/zendesk/tickets/{integration_ns}",
                content=signed["content"],
                headers={
                    "Content-Type": "application/json",
                    "X-Zendesk-Webhook-Signature": signed["signature"],
                    "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                    "X-Zendesk-Webhook-Invocation-Id": "inv-ns",
                },
            )
        # No per-org webhook secret configured → authentication cannot succeed.
        assert r.status_code == 401
    finally:
        await db.rollback()
        await db.execute(
            delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == no_secret_conn.id)
        )
        await db.execute(delete(Organization).where(Organization.id == orphan.id))
        await db.commit()


# ===================================================================
# Signature unit tests: fresh valid / stale / future / invalid
# ===================================================================
def test_verify_zendesk_signature_fresh_valid():
    secret = "unit-secret"
    signed = _sign(secret, {"a": 1}, when=datetime.now(timezone.utc))
    assert (
        verify_zendesk_signature(
            secret=secret,
            timestamp=signed["timestamp"],
            body=signed["content"],
            signature=signed["signature"],
            replay_window_seconds=300,
        )
        is True
    )


def test_verify_zendesk_signature_stale_rejected():
    secret = "unit-secret"
    signed = _sign(
        secret, {"a": 1}, when=datetime.now(timezone.utc) - timedelta(seconds=360)
    )
    assert (
        verify_zendesk_signature(
            secret=secret,
            timestamp=signed["timestamp"],
            body=signed["content"],
            signature=signed["signature"],
            replay_window_seconds=300,
        )
        is False
    )


def test_verify_zendesk_signature_future_rejected():
    secret = "unit-secret"
    signed = _sign(
        secret, {"a": 1}, when=datetime.now(timezone.utc) + timedelta(seconds=360)
    )
    assert (
        verify_zendesk_signature(
            secret=secret,
            timestamp=signed["timestamp"],
            body=signed["content"],
            signature=signed["signature"],
            replay_window_seconds=300,
        )
        is False
    )


def test_verify_zendesk_signature_invalid_rejected():
    signed = _sign("real-secret", {"a": 1}, when=datetime.now(timezone.utc))
    assert (
        verify_zendesk_signature(
            secret="other-secret",
            timestamp=signed["timestamp"],
            body=signed["content"],
            signature=signed["signature"],
            replay_window_seconds=300,
        )
        is False
    )


def test_verify_zendesk_signature_missing_secret_fails_closed():
    signed = _sign("unit-secret", {"a": 1}, when=datetime.now(timezone.utc))
    assert (
        verify_zendesk_signature(
            secret="",
            timestamp=signed["timestamp"],
            body=signed["content"],
            signature=signed["signature"],
            replay_window_seconds=300,
        )
        is False
    )


# ===================================================================
# N. internal (trusted webhook) sync cannot cross tenant
# ===================================================================
@pytest.mark.asyncio
async def test_internal_sync_cannot_cross_tenant(
    monkeypatch, make_client, db, zendesk_env
):
    from app.integrations.zendesk.client import zendesk_client
    from app.models.integration_job import IntegrationJob
    from app.repositories.ticket_repository import TicketRepository

    external_id = 900_444_004
    # Org B owns the local record for this Zendesk id.
    b_ticket = Ticket(
        subject="Org B owned",
        description="Belongs to B",
        status="new",
        priority="normal",
        organization_id=zendesk_env["org_b_id"],
        external_id=str(external_id),
    )
    db.add(b_ticket)
    await db.commit()
    b_ticket_id = b_ticket.id

    async def _get_ticket(db, ticket_id, *, organization_id=None):
        return {
            "ticket": {
                "id": ticket_id,
                "subject": "From webhook sync",
                "description": "desc",
                "requester_id": None,
                "status": "open",
                "priority": "high",
            }
        }

    async def _get_comments(db, ticket_id, *, organization_id=None):
        return {"comments": []}

    async def _update_ticket(db, ticket_id, changes, *, organization_id=None):
        return {"ticket": {"id": ticket_id}}

    monkeypatch.setattr(zendesk_client, "get_ticket", _get_ticket)
    monkeypatch.setattr(zendesk_client, "get_ticket_comments", _get_comments)
    monkeypatch.setattr(zendesk_client, "update_ticket", _update_ticket)

    # Trusted webhook for integration A resolves Org A and enqueues.
    signed = _sign(zendesk_env["secret_a"], _zendesk_payload(external_id))
    invocation = f"inv-sync-{uuid.uuid4().hex}"
    async with make_client() as client:
        r = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": invocation,
            },
        )
    assert r.status_code == 202
    job = await db.get(IntegrationJob, r.json()["job_id"])
    assert job is not None
    assert job.organization_id == zendesk_env["org_a_id"]

    # Execute the job: tenant-bound internal processing.
    from app.services.integration_job_service import IntegrationJobService

    await IntegrationJobService.execute(db=db, job=job)

    try:
        # Org A got its own tenant-owned ticket; Org B's row is untouched.
        a_ticket = await TicketRepository.get_by_external_id_for_tenant(
            db, str(external_id), zendesk_env["org_a_id"]
        )
        assert a_ticket is not None
        assert a_ticket.organization_id == zendesk_env["org_a_id"]
        assert a_ticket.subject == "From webhook sync"

        b_unchanged = await TicketRepository.get_by_id_for_tenant(
            db, b_ticket_id, zendesk_env["org_b_id"]
        )
        assert b_unchanged is not None
        assert b_unchanged.subject == "Org B owned"
        assert b_unchanged.status == "new"
    finally:
        a_ticket_id = a_ticket.id if a_ticket is not None else None
        await db.rollback()
        if a_ticket_id is not None:
            await db.execute(delete(Ticket).where(Ticket.id == a_ticket_id))
        await db.execute(delete(Ticket).where(Ticket.id == b_ticket_id))
        await db.commit()


# ===================================================================
# L. same external ticket id safely in both orgs
# ===================================================================
@pytest.mark.asyncio
async def test_same_external_id_safe_across_orgs(db, zendesk_env):
    external = f"shared-{uuid.uuid4().hex[:10]}"
    ta = Ticket(
        subject="A",
        description="d",
        organization_id=zendesk_env["org_a_id"],
        external_id=external,
    )
    tb = Ticket(
        subject="B",
        description="d",
        organization_id=zendesk_env["org_b_id"],
        external_id=external,
    )
    db.add_all([ta, tb])
    await db.commit()
    ta_id, tb_id = ta.id, tb.id
    try:
        assert ta.id != tb.id
        assert ta.external_id == tb.external_id == external
        # Each org resolves only its own row.
        from app.repositories.ticket_repository import TicketRepository

        a_by_ext = await TicketRepository.get_by_external_id_for_tenant(
            db, external, zendesk_env["org_a_id"]
        )
        b_by_ext = await TicketRepository.get_by_external_id_for_tenant(
            db, external, zendesk_env["org_b_id"]
        )
        assert a_by_ext is not None and a_by_ext.id == ta_id
        assert b_by_ext is not None and b_by_ext.id == tb_id
    finally:
        await db.rollback()
        await db.execute(delete(Ticket).where(Ticket.id.in_([ta_id, tb_id])))
        await db.commit()


# ===================================================================
# M. same customer email safely across orgs; duplicate within org → 409
# ===================================================================
@pytest.mark.asyncio
async def test_same_customer_email_safe_across_orgs(db, zendesk_env):
    from app.repositories.customer_repository import CustomerRepository

    email = f"shared-{uuid.uuid4().hex[:10]}@example.com"
    ca = Customer(name="A", email=email, organization_id=zendesk_env["org_a_id"])
    cb = Customer(name="B", email=email, organization_id=zendesk_env["org_b_id"])
    db.add_all([ca, cb])
    await db.commit()
    ca_id, cb_id = ca.id, cb.id
    try:
        a_by_email = await CustomerRepository.get_by_email_for_tenant(
            db, email, zendesk_env["org_a_id"]
        )
        b_by_email = await CustomerRepository.get_by_email_for_tenant(
            db, email, zendesk_env["org_b_id"]
        )
        assert a_by_email is not None and a_by_email.id == ca_id
        assert b_by_email is not None and b_by_email.id == cb_id
    finally:
        await db.rollback()
        await db.execute(delete(Customer).where(Customer.id.in_([ca_id, cb_id])))
        await db.commit()


@pytest.mark.asyncio
async def test_duplicate_customer_email_same_org_409(make_client, zendesk_env):
    email = f"dup-{uuid.uuid4().hex[:10]}@example.com"

    async with make_client() as client:
        first = await client.post(
            "/customers",
            headers=_h_alpha(),
            json={"name": "First", "email": email},
        )
        second = await client.post(
            "/customers",
            headers=_h_alpha(),
            json={"name": "Second", "email": email},
        )
    assert first.status_code == 201
    assert second.status_code == 409
    first_id = first.json()["id"]

    async with AsyncSessionLocal() as session:
        await session.execute(delete(Customer).where(Customer.id == first_id))
        await session.commit()


# ===================================================================
# O. no token/secret material in logs or responses
# ===================================================================
@pytest.mark.asyncio
async def test_webhook_secret_and_signature_never_logged(
    make_client, zendesk_env, capsys
):
    signed = _sign("attacker-secret", _zendesk_payload(1))

    async with make_client() as client:
        r = await client.post(
            f"/webhooks/zendesk/tickets/{zendesk_env['integration_a']}",
            content=signed["content"],
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signed["signature"],
                "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
                "X-Zendesk-Webhook-Invocation-Id": "inv-log-probe",
            },
        )
    assert r.status_code == 401

    captured = capsys.readouterr()

    assert zendesk_env["secret_a"] not in captured.out + captured.err
    assert zendesk_env["secret_b"] not in captured.out + captured.err
    assert signed["signature"] not in captured.out + captured.err
    assert "access_token" not in captured.out + captured.err
    assert "refresh_token" not in captured.out + captured.err


@pytest.mark.asyncio
async def test_callback_error_response_is_generic(
    monkeypatch, make_client, zendesk_env
):
    from app.services import zendesk_oauth_service

    async def failing_exchange(db, *, code, organization_id, subject):
        raise zendesk_oauth_service.ZendeskOAuthError("provider noise")

    monkeypatch.setattr(
        zendesk_oauth_service.ZendeskOAuthService,
        "exchange_code",
        failing_exchange,
    )

    async with make_client() as client:
        await client.get(
            "/auth/zendesk/login", headers=_h_alpha(), follow_redirects=False
        )
        state = client.cookies.get(STATE_COOKIE)

    r = await _run_callback(make_client, state=state)
    assert r.status_code == 502
    assert "provider noise" not in r.text
    assert "Zendesk authorization failed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_callback_reflects_no_provider_error_param(make_client, zendesk_env):
    async with make_client() as client:
        r = await client.get(
            "/auth/zendesk/callback",
            params={
                "error": "<script>alert(1)</script>",
                "error_description": "xss attempt",
            },
        )
    assert r.status_code == 400
    assert "<script>" not in r.text


# ===================================================================
# Webhook secret provisioning (repository + fail-closed interplay)
# ===================================================================
@pytest.mark.asyncio
async def test_set_webhook_secret_for_organization(db, zendesk_env):
    from app.repositories.zendesk_oauth_token_repository import (
        ZendeskOAuthTokenRepository,
    )

    new_secret = f"provisioned-{uuid.uuid4().hex[:24]}"
    token = await ZendeskOAuthTokenRepository.set_webhook_secret(
        db,
        organization_id=zendesk_env["org_a_id"],
        webhook_secret=new_secret,
    )

    assert token is not None
    assert token.id == zendesk_env["token_a"].id

    stored = await ZendeskOAuthTokenRepository.get_for_organization(
        db, zendesk_env["org_a_id"]
    )
    assert stored is not None
    assert stored.webhook_secret == new_secret

    # The other org's secret is untouched.
    other = await ZendeskOAuthTokenRepository.get_for_organization(
        db, zendesk_env["org_b_id"]
    )
    assert other is not None
    assert other.webhook_secret == zendesk_env["secret_b"]


@pytest.mark.asyncio
async def test_set_webhook_secret_unknown_org_returns_none(db, zendesk_env):
    from app.repositories.zendesk_oauth_token_repository import (
        ZendeskOAuthTokenRepository,
    )

    orphan = Organization(name=f"no-conn-{uuid.uuid4().hex[:8]}")
    db.add(orphan)
    await db.commit()
    orphan_id = orphan.id
    try:
        token = await ZendeskOAuthTokenRepository.set_webhook_secret(
            db,
            organization_id=orphan_id,
            webhook_secret="whatever",
        )
        assert token is None
    finally:
        await db.rollback()
        await db.execute(delete(Organization).where(Organization.id == orphan_id))
        await db.commit()


@pytest.mark.asyncio
async def test_clear_webhook_secret_for_organization(db, zendesk_env):
    from app.repositories.zendesk_oauth_token_repository import (
        ZendeskOAuthTokenRepository,
    )

    token = await ZendeskOAuthTokenRepository.set_webhook_secret(
        db,
        organization_id=zendesk_env["org_a_id"],
        webhook_secret=None,
    )

    assert token is not None
    assert token.webhook_secret is None

    stored = await ZendeskOAuthTokenRepository.get_for_organization(
        db, zendesk_env["org_a_id"]
    )
    assert stored is not None
    assert stored.webhook_secret is None
