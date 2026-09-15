"""Organization membership + tenant context tests.

Tenant resolution is security-critical: membership lookups must always bind
subject AND organization_id. Tests use synthetic subjects (user-alpha,
user-beta) against the app's real database so the uniqueness/FK constraints
are exercised. Each test creates its own organization(s) and removes them on
teardown; no existing local data is touched and no live Nhost is used.

Settings are pinned by a module-level ``_configure`` mirroring tests/test_auth.py
so the HS256 development path is deterministic regardless of any local .env
(AUTH_MODE=jwks) leakage.
"""

import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-tenant-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-tenant-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.principal import AuthenticatedPrincipal
from app.main import app
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.services.tenant_service import (
    TenantAccessDeniedError,
    TenantMembershipAmbiguousError,
    TenantMembershipMissingError,
    resolve_tenant_context,
)

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-tenant-issuer"
TEST_AUDIENCE = "test-tenant-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"

X_TENANT = "X-CXOps-Organization-ID"


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


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    _configure(monkeypatch)
    yield


def _build_token(
    *,
    sub: str,
    secret: str = TEST_SECRET,
) -> str:
    """Build an HS256 token for a synthetic test subject."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": now + 3600,
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def org_scope(db):
    """Create unique test organizations and remove them on teardown."""
    org_ids: list[int] = []

    async def make(subject: str | None = None) -> Organization:
        org = Organization(name=f"tenant-test-{uuid.uuid4().hex[:10]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        if subject is not None:
            db.add(
                OrganizationMembership(
                    subject=subject,
                    organization_id=org.id,
                )
            )
        await db.commit()
        return org

    yield make

    if org_ids:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
        await db.commit()


def _principal(subject: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(subject=subject)


# ------------------------------------------------------ resolution service


@pytest.mark.asyncio
async def test_single_membership_resolves_tenant(db, org_scope):
    org = await org_scope(subject=USER_ALPHA)

    tenant = await resolve_tenant_context(db, _principal(USER_ALPHA))

    assert tenant.organization_id == org.id
    assert tenant.subject == USER_ALPHA


@pytest.mark.asyncio
async def test_no_membership_denied(db, org_scope):
    await org_scope()  # org exists, but nothing belongs to user-alpha

    with pytest.raises(TenantMembershipMissingError):
        await resolve_tenant_context(db, _principal(USER_ALPHA))


@pytest.mark.asyncio
async def test_cannot_cross_into_another_users_organization(db, org_scope):
    org_alpha = await org_scope(subject=USER_ALPHA)
    org_beta = await org_scope(subject=USER_BETA)

    with pytest.raises(TenantAccessDeniedError):
        await resolve_tenant_context(
            db,
            _principal(USER_ALPHA),
            requested_organization_id=org_beta.id,
        )

    tenant = await resolve_tenant_context(
        db,
        _principal(USER_ALPHA),
        requested_organization_id=org_alpha.id,
    )
    assert tenant.organization_id == org_alpha.id


@pytest.mark.asyncio
async def test_valid_requested_organization_accepted(db, org_scope):
    org_a = await org_scope(subject=USER_ALPHA)
    org_b = await org_scope(subject=USER_ALPHA)

    tenant = await resolve_tenant_context(
        db,
        _principal(USER_ALPHA),
        requested_organization_id=org_b.id,
    )
    assert tenant.organization_id == org_b.id
    assert org_a.id != org_b.id


@pytest.mark.asyncio
async def test_multiple_memberships_require_selector(db, org_scope):
    org_a = await org_scope(subject=USER_ALPHA)
    org_b = await org_scope(subject=USER_ALPHA)

    with pytest.raises(TenantMembershipAmbiguousError):
        await resolve_tenant_context(db, _principal(USER_ALPHA))

    tenant = await resolve_tenant_context(
        db,
        _principal(USER_ALPHA),
        requested_organization_id=org_a.id,
    )
    assert tenant.organization_id == org_a.id
    assert org_a.id != org_b.id


def test_membership_schema_has_no_token_columns():
    columns = {column.name for column in OrganizationMembership.__table__.columns}
    assert columns == {"id", "organization_id", "subject", "created_at"}


@pytest.mark.asyncio
async def test_membership_row_persists_only_minimal_fields(db, org_scope):
    org = await org_scope()
    membership = OrganizationMembership(
        subject=USER_ALPHA,
        organization_id=org.id,
    )
    db.add(membership)
    await db.commit()

    row = await db.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.subject == USER_ALPHA,
            OrganizationMembership.organization_id == org.id,
        )
    )
    stored = row.scalar_one()

    assert stored.subject == USER_ALPHA
    assert stored.organization_id == org.id
    assert stored.created_at is not None
    assert not hasattr(stored, "access_token")
    assert not hasattr(stored, "refresh_token")
    assert not hasattr(stored, "jwt")


@pytest.mark.asyncio
async def test_duplicate_membership_rejected_by_constraint(db, org_scope):
    org = await org_scope()

    db.add(
        OrganizationMembership(
            subject=USER_ALPHA,
            organization_id=org.id,
        )
    )
    await db.commit()

    db.add(
        OrganizationMembership(
            subject=USER_ALPHA,
            organization_id=org.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# ------------------------------------------------------- proof endpoint


@pytest.mark.asyncio
async def test_proof_endpoint_unauthenticated_401(client):
    async with client:
        response = await client.get("/me/tenant")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_proof_endpoint_authenticated_no_membership_403(client, org_scope):
    await org_scope()  # some org exists, user-alpha is not in it
    headers = {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}

    async with client:
        response = await client.get("/me/tenant", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_proof_endpoint_valid_membership_200(client, org_scope):
    org = await org_scope(subject=USER_ALPHA)
    headers = {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}

    async with client:
        response = await client.get("/me/tenant", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == org.id
    assert body["organization_name"] == org.name
    assert "subject" not in body


@pytest.mark.asyncio
async def test_forged_tenant_selector_rejected(client, org_scope):
    org_alpha = await org_scope(subject=USER_ALPHA)
    org_beta = await org_scope(subject=USER_BETA)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: str(org_beta.id),
    }

    async with client:
        response = await client.get("/me/tenant", headers=headers)
    assert response.status_code == 403
    assert org_alpha.id != org_beta.id


@pytest.mark.asyncio
async def test_endpoint_accepts_valid_requested_organization(client, org_scope):
    org_a = await org_scope(subject=USER_ALPHA)
    org_b = await org_scope(subject=USER_ALPHA)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: str(org_b.id),
    }

    async with client:
        response = await client.get("/me/tenant", headers=headers)
    assert response.status_code == 200
    assert response.json()["organization_id"] == org_b.id
    assert org_a.id != org_b.id


@pytest.mark.asyncio
async def test_endpoint_multiple_memberships_409_without_selector(client, org_scope):
    await org_scope(subject=USER_ALPHA)
    await org_scope(subject=USER_ALPHA)
    headers = {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}

    async with client:
        response = await client.get("/me/tenant", headers=headers)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_endpoint_multiple_memberships_200_with_valid_selector(client, org_scope):
    org_b = await org_scope(subject=USER_ALPHA)
    await org_scope(subject=USER_ALPHA)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: str(org_b.id),
    }

    async with client:
        response = await client.get("/me/tenant", headers=headers)
    assert response.status_code == 200
    assert response.json()["organization_id"] == org_b.id


@pytest.mark.asyncio
async def test_malformed_selector_rejected_403(client, org_scope):
    await org_scope(subject=USER_ALPHA)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: "not-an-int",
    }

    async with client:
        response = await client.get("/me/tenant", headers=headers)
    assert response.status_code == 403
