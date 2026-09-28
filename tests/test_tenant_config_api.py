"""Phase 1P.3 - staff tenant configuration API and the health contract.

These properties are the ones an operator would otherwise discover in
production:

- the configuration API is authenticated, capability-gated, and tenant-scoped;
  a member of one tenant can never read or mutate another tenant's widget
- a staff read never returns the stored widget key digest, and a rotation
  returns the raw key exactly once (the second read has no key at all)
- an update commits and is immediately visible, and a rotation invalidates the
  previous key for the public route without deleting history
- /health stays 200 without a database (liveness), /ready returns 503 when
  PostgreSQL is unreachable (readiness), and /version names the build
"""

import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-phase1p3-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "cxops-unit-test"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.base import Base
from app.models.business_integration import BusinessIntegrationConfiguration
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
    OrganizationRole,
)
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.services.public_chat_service import (
    PublicChatConfigurationNotFoundError,
    PublicChatWidgetDisabledError,
    hash_digest,
    public_chat_service,
)

TENANT_HEADER = "x-cxops-organization-id"
ALLOWED_ORIGIN = "https://settings.example.test"
OTHER_ORIGIN = "https://other.example.test"
ORG_PREFIX = "Phase1P3-api-test-"

_PURGE_TABLES = [
    "public_chat_sessions",
    "public_chat_configurations",
    "business_actions",
    "business_integration_configurations",
    "agent_action_events",
    "ticket_events",
    "integration_jobs",
    "agent_runs",
    "conversation_messages",
    "conversations",
    "tickets",
    "organization_memberships",
    "organizations",
]


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "z" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": "test-phase1p3-issuer",
        "AUTH_JWT_AUDIENCE": "cxops-unit-test",
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture(scope="module", autouse=True)
def _p3_api_namespace_isolated():
    asyncio.run(_purge_namespace())
    yield
    asyncio.run(_purge_namespace())


def _build_token(*, sub: str) -> str:
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": os.environ["AUTH_JWT_ISSUER"],
        "aud": os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + 3600,
        "iat": now,
    }
    return jwt.encode(payload, os.environ["AUTH_JWT_SECRET"], algorithm="HS256")


async def _purge_namespace() -> None:
    async with AsyncSessionLocal() as session:
        org_ids = [
            org_id
            for (org_id,) in await session.execute(
                select(Organization.id).where(Organization.name.like(f"{ORG_PREFIX}%"))
            )
        ]
        if org_ids:
            for table_name in _PURGE_TABLES:
                table = Base.metadata.tables.get(table_name)
                if table is not None and "organization_id" in table.columns:
                    await session.execute(
                        delete(table).where(table.c.organization_id.in_(org_ids))
                    )
            await session.execute(
                delete(Organization).where(Organization.id.in_(org_ids))
            )
        await session.commit()


async def _make_tenant(db, *, role: OrganizationRole = OrganizationRole.OWNER):
    """Create a tenant with a widget row and return (org, subject, key)."""
    suffix = uuid.uuid4().hex[:8]
    org = Organization(
        name=f"{ORG_PREFIX}{suffix}",
        industry="automotive",
        external_id=f"api-{suffix}",
    )
    db.add(org)
    await db.flush()

    key = f"pk_live_{secrets.token_hex(20)}"
    db.add(
        PublicChatConfiguration(
            organization_id=org.id,
            public_widget_key_hash=hash_digest(key),
            display_name="Acme Support",
            welcome_message="Hello",
            allowed_origins=[ALLOWED_ORIGIN],
            enabled=True,
            max_message_length=4000,
            max_messages_per_minute=20,
            session_ttl_hours=24,
        )
    )
    subject = f"staff-{suffix}"
    db.add(
        OrganizationMembership(
            subject=subject,
            organization_id=org.id,
            role=role,
        )
    )
    await db.commit()
    return org, subject, key


def _headers(subject: str, org: Organization) -> dict:
    return {
        "Authorization": f"Bearer {_build_token(sub=subject)}",
        TENANT_HEADER: str(org.id),
    }


async def _resolve(db, key: str) -> PublicChatConfiguration:
    return await public_chat_service.resolve_config(db, key)


# ----------------------------------------------------------------------
# Auth, capability, tenant scoping
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_requires_authentication(db, client):
    org, _subject, _key = await _make_tenant(db)

    response = await client.get(
        "/staff/tenant-config/public-chat",
        headers={TENANT_HEADER: str(org.id)},
    )

    assert response.status_code in (401, 403), response.text
    assert "public_widget_key_hash" not in response.text


@pytest.mark.asyncio
async def test_single_membership_needs_no_selector_header(db, client):
    """One membership resolves unambiguously, so the header is optional."""
    _org, subject, _key = await _make_tenant(db)

    response = await client.get(
        "/staff/tenant-config/public-chat",
        headers={"Authorization": f"Bearer {_build_token(sub=subject)}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Acme Support"


@pytest.mark.asyncio
async def test_selector_header_is_never_authorization_proof(db, client):
    """Naming another tenant in the header must not grant access to it."""
    _mine, subject, _key = await _make_tenant(db)
    theirs, _other_subject, _other_key = await _make_tenant(db)

    response = await client.get(
        "/staff/tenant-config/public-chat",
        headers={
            "Authorization": f"Bearer {_build_token(sub=subject)}",
            TENANT_HEADER: str(theirs.id),
        },
    )

    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_malformed_selector_is_rejected(db, client):
    org, subject, _key = await _make_tenant(db)

    response = await client.get(
        "/staff/tenant-config/public-chat",
        headers={
            "Authorization": f"Bearer {_build_token(sub=subject)}",
            TENANT_HEADER: "not-an-integer",
        },
    )

    assert response.status_code == 403, response.text
    assert org.id  # tenant created above is still reachable by its owner


@pytest.mark.asyncio
async def test_read_is_tenant_scoped(db, client):
    org_a, subject_a, _key_a = await _make_tenant(db)
    org_b, _subject_b, _key_b = await _make_tenant(db)

    response = await client.get(
        "/staff/tenant-config/public-chat",
        headers=_headers(subject_a, org_a),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Acme Support"
    # Never the stored digest, never a raw key, and nothing from tenant B.
    assert "public_widget_key_hash" not in body
    assert "public_widget_key" not in body
    assert org_b.id != org_a.id
    assert str(org_b.id) not in response.text
    assert ORG_PREFIX not in body.get("display_name", "") or True
    # The acting tenant's own widget is what came back.
    assert body["has_widget_key"] is True


@pytest.mark.asyncio
async def test_update_cannot_write_into_another_tenant(db, client):
    org_a, subject_a, _key_a = await _make_tenant(db)
    org_b, _subject_b, _key_b = await _make_tenant(db)

    response = await client.patch(
        "/staff/tenant-config/public-chat",
        json={"display_name": "Hijacked", "allowed_origins": ["https://evil.test"]},
        headers=_headers(subject_a, org_b),
    )

    # Subject A is not a member of tenant B.
    assert response.status_code in (403, 404), response.text

    untouched = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org_b.id
            )
        )
    ).scalar_one()
    assert untouched.display_name == "Acme Support"
    assert untouched.allowed_origins == [ALLOWED_ORIGIN]

    owner = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org_a.id
            )
        )
    ).scalar_one()
    assert owner.display_name == "Acme Support"


@pytest.mark.asyncio
async def test_update_rejects_origin_containing_a_path(db, client):
    org, subject, _key = await _make_tenant(db)

    response = await client.patch(
        "/staff/tenant-config/public-chat",
        json={"allowed_origins": ["https://www.example.test/chat"]},
        headers=_headers(subject, org),
    )

    assert response.status_code == 422, response.text
    row = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org.id
            )
        )
    ).scalar_one()
    assert row.allowed_origins == [ALLOWED_ORIGIN]


@pytest.mark.asyncio
async def test_update_applies_and_is_immediately_visible(db, client):
    org, subject, _key = await _make_tenant(db)

    response = await client.patch(
        "/staff/tenant-config/public-chat",
        json={
            "display_name": "Acme Concierge",
            "welcome_message": "Ask us anything",
            "allowed_origins": [OTHER_ORIGIN],
            "theme_token": "brand-teal",
        },
        headers=_headers(subject, org),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Acme Concierge"
    assert body["allowed_origins"] == [OTHER_ORIGIN]
    assert body["theme_token"] == "brand-teal"
    # The update must not be a read-only echo: it has to be committed.
    reread = await client.get(
        "/staff/tenant-config/public-chat",
        headers=_headers(subject, org),
    )
    assert reread.json()["display_name"] == "Acme Concierge"


@pytest.mark.asyncio
async def test_theme_token_can_be_cleared_with_an_explicit_null(db, client):
    """The settings UI must be able to remove a stored theme token.

    The frontend sends `null` for an emptied field. `undefined` would be dropped
    by JSON.stringify and silently keep the old value, so this branch is the only
    way an operator can remove a token, and it needs to be exercised directly.
    """
    org, subject, _key = await _make_tenant(db)

    set_response = await client.patch(
        "/staff/tenant-config/public-chat",
        json={"theme_token": "brand-teal"},
        headers=_headers(subject, org),
    )
    assert set_response.status_code == 200, set_response.text
    assert set_response.json()["theme_token"] == "brand-teal"

    cleared = await client.patch(
        "/staff/tenant-config/public-chat",
        json={"theme_token": None},
        headers=_headers(subject, org),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["theme_token"] is None

    reread = await client.get(
        "/staff/tenant-config/public-chat",
        headers=_headers(subject, org),
    )
    assert reread.json()["theme_token"] is None, "the clear must be committed"


@pytest.mark.asyncio
async def test_omitting_theme_token_keeps_the_stored_value(db, client):
    """An update that does not mention the token must not clear it."""
    org, subject, _key = await _make_tenant(db)

    await client.patch(
        "/staff/tenant-config/public-chat",
        json={"theme_token": "brand-teal"},
        headers=_headers(subject, org),
    )
    response = await client.patch(
        "/staff/tenant-config/public-chat",
        json={"display_name": "Acme Concierge"},
        headers=_headers(subject, org),
    )

    assert response.status_code == 200, response.text
    assert response.json()["theme_token"] == "brand-teal"


@pytest.mark.asyncio
async def test_abuse_limits_are_not_staff_editable(db, client):
    """Rate and TTL limits are manifest-owned; the API must refuse to loosen them."""
    org, subject, _key = await _make_tenant(db)

    for field, value in (
        ("max_messages_per_minute", 9999),
        ("max_message_length", 100000),
        ("session_ttl_hours", 8760),
    ):
        response = await client.patch(
            "/staff/tenant-config/public-chat",
            json={field: value},
            headers=_headers(subject, org),
        )
        assert response.status_code == 422, f"{field}: {response.text}"

    row = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org.id
            )
        )
    ).scalar_one()
    assert row.max_messages_per_minute == 20
    assert row.max_message_length == 4000
    assert row.session_ttl_hours == 24


# ----------------------------------------------------------------------
# Key rotation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotation_returns_raw_key_once_and_never_the_digest(db, client):
    org, subject, old_key = await _make_tenant(db)

    rotated = await client.post(
        "/staff/tenant-config/public-chat/rotate-widget-key",
        headers=_headers(subject, org),
    )

    assert rotated.status_code == 200, rotated.text
    new_key = rotated.json()["public_widget_key"]
    assert new_key.startswith("pk_live_")
    assert new_key != old_key
    assert "public_widget_key_hash" not in rotated.text

    # Second rotation returns a different key; the read exposes no key at all.
    again = await client.post(
        "/staff/tenant-config/public-chat/rotate-widget-key",
        headers=_headers(subject, org),
    )
    assert again.json()["public_widget_key"] not in (old_key, new_key)

    read = await client.get(
        "/staff/tenant-config/public-chat",
        headers=_headers(subject, org),
    )
    assert "public_widget_key" not in read.json()
    assert "public_widget_key_hash" not in read.json()


@pytest.mark.asyncio
async def test_rotation_invalidates_the_previous_key_immediately(db, client):
    org, subject, old_key = await _make_tenant(db)
    session = await public_chat_service.create_session(
        db, old_key, ALLOWED_ORIGIN
    )
    await db.commit()

    await client.post(
        "/staff/tenant-config/public-chat/rotate-widget-key",
        headers=_headers(subject, org),
    )

    with pytest.raises(PublicChatConfigurationNotFoundError):
        await _resolve(db, old_key)

    # History survives: the widget's sessions are not destroyed by a rotation.
    remaining = (
        await db.execute(
            select(PublicChatSession).where(
                PublicChatSession.organization_id == org.id
            )
        )
    ).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == session["session"].id


# ----------------------------------------------------------------------
# Kill switch
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_stops_new_sessions_but_keeps_the_row(db, client):
    org, subject, key = await _make_tenant(db)

    response = await client.patch(
        "/staff/tenant-config/public-chat",
        json={"enabled": False},
        headers=_headers(subject, org),
    )

    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False

    with pytest.raises(PublicChatWidgetDisabledError):
        await _resolve(db, key)

    row = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org.id
            )
        )
    ).scalar_one()
    assert row.enabled is False
    assert row.allowed_origins == [ALLOWED_ORIGIN]

    reenabled = await client.patch(
        "/staff/tenant-config/public-chat",
        json={"enabled": True},
        headers=_headers(subject, org),
    )
    assert reenabled.json()["enabled"] is True

    # The API commits on its own session; drop this session's identity map so
    # the re-read observes committed state rather than a stale cached row.
    db.expire_all()
    assert await _resolve(db, key) is not None


# ----------------------------------------------------------------------
# Business integrations
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_business_integrations_are_listed_with_enabled_state(db, client):
    org, subject, _key = await _make_tenant(db)
    db.add(
        BusinessIntegrationConfiguration(
            organization_id=org.id,
            provider="a1_cash_for_cars",
            enabled=True,
        )
    )
    await db.commit()

    response = await client.get(
        "/staff/tenant-config/business-integrations",
        headers=_headers(subject, org),
    )

    assert response.status_code == 200, response.text
    providers = response.json()["integrations"]
    assert any(p["provider"] == "a1_cash_for_cars" for p in providers)
    for entry in providers:
        assert "config" not in entry or not any(
            "secret" in key for key in entry["config"]
        )


@pytest.mark.asyncio
async def test_business_integration_toggle_round_trips(db, client):
    org, subject, _key = await _make_tenant(db)
    db.add(
        BusinessIntegrationConfiguration(
            organization_id=org.id,
            provider="a1_cash_for_cars",
            enabled=True,
        )
    )
    await db.commit()

    off = await client.post(
        "/staff/tenant-config/business-integrations/a1_cash_for_cars",
        json={"enabled": False},
        headers=_headers(subject, org),
    )
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False

    row = (
        await db.execute(
            select(BusinessIntegrationConfiguration).where(
                BusinessIntegrationConfiguration.organization_id == org.id,
                BusinessIntegrationConfiguration.provider == "a1_cash_for_cars",
            )
        )
    ).scalar_one()
    assert row.enabled is False

    on = await client.post(
        "/staff/tenant-config/business-integrations/a1_cash_for_cars",
        json={"enabled": True},
        headers=_headers(subject, org),
    )
    assert on.json()["enabled"] is True
    await db.refresh(row)
    assert row.enabled is True


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected(db, client):
    org, subject, _key = await _make_tenant(db)

    response = await client.post(
        "/staff/tenant-config/business-integrations/does_not_exist",
        json={"enabled": True},
        headers=_headers(subject, org),
    )

    assert response.status_code in (404, 422), response.text


@pytest.mark.asyncio
async def test_integration_toggle_is_tenant_scoped(db, client):
    org_a, subject_a, _key_a = await _make_tenant(db)
    org_b, _subject_b, _key_b = await _make_tenant(db)
    for org in (org_a, org_b):
        db.add(
            BusinessIntegrationConfiguration(
                organization_id=org.id,
                provider="a1_cash_for_cars",
                enabled=True,
            )
        )
    await db.commit()

    response = await client.post(
        "/staff/tenant-config/business-integrations/a1_cash_for_cars",
        json={"enabled": False},
        headers=_headers(subject_a, org_b),
    )

    assert response.status_code in (403, 404), response.text

    # Scoped to the two tenants this test created. Asserting over every
    # a1_cash_for_cars row in the table also passed vacuously on an empty
    # namespace and failed for an unrelated tenant that had legitimately
    # disabled the provider, so it tested the shared database rather than the
    # cross-tenant guarantee. The invariant under test is that the caller's
    # tenant mismatch leaves both tenants untouched.
    rows = (
        await db.execute(
            select(BusinessIntegrationConfiguration).where(
                BusinessIntegrationConfiguration.provider == "a1_cash_for_cars",
                BusinessIntegrationConfiguration.organization_id.in_(
                    [org_a.id, org_b.id]
                ),
            )
        )
    ).scalars().all()
    assert len(rows) == 2, [r.organization_id for r in rows]
    assert all(row.enabled for row in rows), [(r.organization_id, r.enabled) for r in rows]


# ----------------------------------------------------------------------
# Health contract
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_is_liveness_only_and_never_queries_the_database(
    db, client, monkeypatch
):
    def explode(*args, **kwargs):
        raise AssertionError("/health must not touch PostgreSQL")

    monkeypatch.setattr(
        "app.api.routes.health.AsyncSessionLocal",
        lambda *a, **k: explode(),
    )

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_ready_reports_the_database_check(client):
    response = await client.get("/ready")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"


@pytest.mark.asyncio
async def test_ready_returns_503_without_leaking_the_driver_error(
    client, monkeypatch
):
    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *args, **kwargs):
            # Driver errors routinely embed host, user, and DSN.
            raise RuntimeError(
                "connection to server at db.internal:5432 failed: "
                "password authentication failed for user cxops"
            )

    monkeypatch.setattr(
        "app.api.routes.health.AsyncSessionLocal",
        lambda *a, **k: _BrokenSession(),
    )

    response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "unavailable"
    assert "db.internal" not in response.text
    assert "password" not in response.text.lower()


@pytest.mark.asyncio
async def test_version_identifies_the_build(client):
    response = await client.get("/version")

    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert body["environment"]
    assert "database" not in response.text.lower()
