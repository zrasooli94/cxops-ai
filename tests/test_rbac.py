"""RBAC foundation tests (Phase 1D.1).

Tests the role model, capability matrix, authorization context resolution,
proof endpoint, and role-spoofing resistance. All tests are deterministic and
run against the local database with synthetic JWT subjects.
"""

import os
import time
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-rbac-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-rbac-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.principal import AuthenticatedPrincipal
from app.core.rbac import (
    ALL_CAPABILITIES,
    READ_ONLY_CAPABILITIES,
    ROLE_CAPABILITIES,
    AuthorizationContext,
    Capability,
    MissingCapabilityError,
    OrganizationRole,
    capabilities_for_role,
    has_capability,
    require_capability,
)
from app.main import app
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.repositories.organization_membership_repository import (
    OrganizationMembershipRepository,
)
from app.schemas.organization import OrganizationCreate
from app.services.authorization_service import (
    AuthorizationMembershipMissingError,
    AuthorizationRoleInvalidError,
    resolve_authorization_context,
)
from app.services.organization_service import OrganizationService
from scripts.bootstrap_tenant import bootstrap as bootstrap_membership

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-rbac-issuer"
TEST_AUDIENCE = "test-rbac-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"
X_TENANT = "X-CXOps-Organization-ID"


def _configure(monkeypatch, **overrides) -> None:
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
    **extra_claims,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": now + 3600,
        "iat": now,
        **extra_claims,
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

    async def make(
        subject: str | None = None,
        role: OrganizationRole = OrganizationRole.OWNER,
    ) -> Organization:
        org = Organization(name=f"rbac-test-{uuid.uuid4().hex[:10]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        if subject is not None:
            db.add(
                OrganizationMembership(
                    subject=subject,
                    organization_id=org.id,
                    role=role,
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


# ---------------------------------------------------------------------- role enum


def test_organization_role_values():
    assert OrganizationRole.OWNER.value == "owner"
    assert OrganizationRole.ADMIN.value == "admin"
    assert OrganizationRole.SUPERVISOR.value == "supervisor"
    assert OrganizationRole.AGENT.value == "agent"
    assert OrganizationRole.VIEWER.value == "viewer"


# ----------------------------------------------------------------- capability matrix


def test_owner_has_all_capabilities():
    assert capabilities_for_role(OrganizationRole.OWNER) == ALL_CAPABILITIES


def test_admin_has_all_capabilities():
    assert capabilities_for_role(OrganizationRole.ADMIN) == ALL_CAPABILITIES


def test_supervisor_matrix():
    expected = {
        Capability.CUSTOMER_READ,
        Capability.CUSTOMER_WRITE,
        Capability.TICKET_READ,
        Capability.TICKET_WRITE,
        Capability.AGENT_RUN,
        Capability.AGENT_APPROVE,
        Capability.AGENT_EXECUTE,
        Capability.KNOWLEDGE_READ,
        Capability.KNOWLEDGE_MANAGE,
        Capability.AUTOMATION_READ,
        Capability.OBSERVABILITY_READ,
        Capability.INTEGRATION_READ,
        Capability.MEMBER_READ,
    }
    assert ROLE_CAPABILITIES[OrganizationRole.SUPERVISOR] == frozenset(expected)


def test_agent_matrix():
    expected = {
        Capability.CUSTOMER_READ,
        Capability.TICKET_READ,
        Capability.TICKET_WRITE,
        Capability.AGENT_RUN,
        Capability.KNOWLEDGE_READ,
        Capability.INTEGRATION_READ,
    }
    assert ROLE_CAPABILITIES[OrganizationRole.AGENT] == frozenset(expected)


def test_viewer_matrix_read_only():
    assert ROLE_CAPABILITIES[OrganizationRole.VIEWER] == READ_ONLY_CAPABILITIES


def test_unknown_role_fails_closed():
    assert capabilities_for_role("hacker") == frozenset()
    authz = AuthorizationContext(
        organization_id=1, subject="sub", role="hacker"
    )
    assert not has_capability(authz, Capability.TICKET_WRITE)


# ---------------------------------------------------------------- capability helpers


def test_has_capability_fail_closed():
    authz = AuthorizationContext(
        organization_id=1, subject="sub", role=OrganizationRole.VIEWER
    )
    assert has_capability(authz, Capability.TICKET_READ)
    assert not has_capability(authz, Capability.TICKET_WRITE)
    assert not has_capability(None, Capability.TICKET_READ)

def test_require_capability_raises_when_missing():
    authz = AuthorizationContext(
        organization_id=1, subject="sub", role=OrganizationRole.VIEWER
    )
    with pytest.raises(MissingCapabilityError):
        require_capability(authz, Capability.TICKET_WRITE)


def test_require_capability_succeeds_when_present():
    authz = AuthorizationContext(
        organization_id=1, subject="sub", role=OrganizationRole.ADMIN
    )
    require_capability(authz, Capability.MEMBER_MANAGE)


# ---------------------------------------------------------- membership / creation


@pytest.mark.asyncio
async def test_organization_creator_receives_owner(db):
    org = await OrganizationService.create_with_membership(
        db,
        OrganizationCreate(name=f"creator-test-{uuid.uuid4().hex[:8]}"),
        USER_ALPHA,
    )

    try:
        row = await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.subject == USER_ALPHA,
                OrganizationMembership.organization_id == org.id,
            )
        )
        membership = row.scalar_one()
        assert membership.role == OrganizationRole.OWNER.value
    finally:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == org.id
            )
        )
        await db.execute(delete(Organization).where(Organization.id == org.id))
        await db.commit()


@pytest.mark.asyncio
async def test_bootstrap_creates_requested_role(db, org_scope):
    org = await org_scope()
    await bootstrap_membership(
        subject=USER_ALPHA,
        organization_id=org.id,
        role=OrganizationRole.SUPERVISOR,
    )

    async with AsyncSessionLocal() as check_db:
        membership = (
            await OrganizationMembershipRepository.get_for_subject_and_organization(
                check_db, USER_ALPHA, org.id
            )
        )
        assert membership is not None
        assert membership.role == OrganizationRole.SUPERVISOR.value


@pytest.mark.asyncio
async def test_repository_create_requires_explicit_role(db, org_scope):
    org = await org_scope()
    with pytest.raises(TypeError):
        await OrganizationMembershipRepository.create(
            db,
            subject=USER_ALPHA,
            organization_id=org.id,
        )


@pytest.mark.asyncio
async def test_repository_create_accepts_explicit_role(db, org_scope):
    org = await org_scope()
    membership = await OrganizationMembershipRepository.create(
        db,
        subject=USER_ALPHA,
        organization_id=org.id,
        role=OrganizationRole.AGENT,
    )
    await db.commit()
    assert membership.role == OrganizationRole.AGENT


@pytest.mark.asyncio
async def test_missing_role_insert_fails_closed_not_owner(db, org_scope):
    """Omitting the role must fail, not silently grant owner."""
    org = await org_scope()
    db.add(
        OrganizationMembership(
            subject=USER_ALPHA,
            organization_id=org.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


def test_role_column_has_no_implicit_default():
    col = OrganizationMembership.__table__.columns["role"]
    assert col.default is None
    assert col.server_default is None
    assert not col.nullable


@pytest.mark.asyncio
async def test_database_constraint_rejects_invalid_role(db, org_scope):
    org = await org_scope()
    db.add(
        OrganizationMembership(
            subject=USER_ALPHA,
            organization_id=org.id,
            role="emperor",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# ----------------------------------------------------------- authorization resolution


@pytest.mark.asyncio
async def test_authorization_resolves_for_owner(db, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    tenant_ctx = type("Tenant", (), {"organization_id": org.id, "subject": USER_ALPHA})()
    authz = await resolve_authorization_context(db, _principal(USER_ALPHA), tenant_ctx)
    assert authz.role == OrganizationRole.OWNER
    assert authz.organization_id == org.id
    assert authz.subject == USER_ALPHA


@pytest.mark.asyncio
async def test_authorization_role_scoped_to_membership(db, org_scope):
    org_a = await org_scope(subject=USER_ALPHA, role=OrganizationRole.ADMIN)
    org_b = await org_scope(subject=USER_ALPHA, role=OrganizationRole.VIEWER)

    tenant_a = type("Tenant", (), {"organization_id": org_a.id, "subject": USER_ALPHA})()
    tenant_b = type("Tenant", (), {"organization_id": org_b.id, "subject": USER_ALPHA})()

    authz_a = await resolve_authorization_context(db, _principal(USER_ALPHA), tenant_a)
    authz_b = await resolve_authorization_context(db, _principal(USER_ALPHA), tenant_b)

    assert authz_a.role == OrganizationRole.ADMIN
    assert authz_b.role == OrganizationRole.VIEWER


@pytest.mark.asyncio
async def test_switching_tenant_changes_role(db, org_scope):
    org_a = await org_scope(subject=USER_ALPHA, role=OrganizationRole.SUPERVISOR)
    org_b = await org_scope(subject=USER_ALPHA, role=OrganizationRole.AGENT)

    tenant_a = type("Tenant", (), {"organization_id": org_a.id, "subject": USER_ALPHA})()
    tenant_b = type("Tenant", (), {"organization_id": org_b.id, "subject": USER_ALPHA})()

    authz_a = await resolve_authorization_context(db, _principal(USER_ALPHA), tenant_a)
    authz_b = await resolve_authorization_context(db, _principal(USER_ALPHA), tenant_b)

    assert authz_a.role == OrganizationRole.SUPERVISOR
    assert authz_b.role == OrganizationRole.AGENT


@pytest.mark.asyncio
async def test_missing_membership_fails_closed_after_tenant_resolution(db, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    tenant = type("Tenant", (), {"organization_id": org.id, "subject": USER_ALPHA})()

    # Revoke membership after tenant resolution
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.subject == USER_ALPHA,
            OrganizationMembership.organization_id == org.id,
        )
    )
    await db.commit()

    with pytest.raises(AuthorizationMembershipMissingError):
        await resolve_authorization_context(db, _principal(USER_ALPHA), tenant)


@pytest.mark.asyncio
async def test_invalid_persisted_role_fails_closed(db, org_scope, monkeypatch):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    membership = (
        await OrganizationMembershipRepository.get_for_subject_and_organization(
            db, USER_ALPHA, org.id
        )
    )
    membership.role = "hacker"

    async def fake_lookup(*_args, **_kwargs):
        return membership

    monkeypatch.setattr(
        OrganizationMembershipRepository,
        "get_for_subject_and_organization",
        fake_lookup,
    )

    tenant = type("Tenant", (), {"organization_id": org.id, "subject": USER_ALPHA})()
    with pytest.raises(AuthorizationRoleInvalidError):
        await resolve_authorization_context(db, _principal(USER_ALPHA), tenant)

    await db.rollback()


# --------------------------------------------------------------- proof endpoint


@pytest.mark.asyncio
async def test_proof_endpoint_unauthenticated_401(client):
    async with client:
        response = await client.get("/me/authorization")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_proof_endpoint_returns_safe_data(client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.SUPERVISOR)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: str(org.id),
    }

    async with client:
        response = await client.get("/me/authorization", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == org.id
    assert body["role"] == "supervisor"
    assert Capability.TICKET_WRITE.value in body["capabilities"]
    assert Capability.ORGANIZATION_MANAGE.value not in body["capabilities"]
    assert "subject" not in body
    assert "token" not in body


@pytest.mark.asyncio
async def test_proof_endpoint_for_viewer(client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.VIEWER)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: str(org.id),
    }

    async with client:
        response = await client.get("/me/authorization", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == org.id
    assert body["role"] == "viewer"
    assert set(body["capabilities"]) == {
        c.value for c in READ_ONLY_CAPABILITIES
    }


# --------------------------------------------------------------- role spoofing


@pytest.mark.asyncio
async def test_forged_role_header_ignored(client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.AGENT)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        "X-CXOps-Role": "owner",
        X_TENANT: str(org.id),
    }

    async with client:
        response = await client.get("/me/authorization", headers=headers)

    assert response.status_code == 200
    assert response.json()["organization_id"] == org.id
    assert response.json()["role"] == "agent"


@pytest.mark.asyncio
async def test_forged_jwt_role_claim_ignored(client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.VIEWER)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA, role='admin')}",
        X_TENANT: str(org.id),
    }

    async with client:
        response = await client.get("/me/authorization", headers=headers)

    assert response.status_code == 200
    assert response.json()["organization_id"] == org.id
    assert response.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_request_payload_cannot_change_role(client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    headers = {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}
    payload = {
        "name": f"spoof-{uuid.uuid4().hex[:8]}",
        "role": "viewer",
    }

    async with client:
        response = await client.post("/organizations", json=payload, headers=headers)

    assert response.status_code == 201
    created = response.json()
    assert created["id"] != org.id

    try:
        async with AsyncSessionLocal() as check_db:
            row = await check_db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.subject == USER_ALPHA,
                    OrganizationMembership.organization_id == created["id"],
                )
            )
            membership = row.scalar_one()
            assert membership.role == OrganizationRole.OWNER.value
    finally:
        async with AsyncSessionLocal() as cleanup_db:
            await cleanup_db.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.organization_id == created["id"]
                )
            )
            await cleanup_db.execute(
                delete(Organization).where(Organization.id == created["id"])
            )
            await cleanup_db.commit()


@pytest.mark.asyncio
async def test_forged_organization_selector_still_403(client, org_scope):
    org_alpha = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    org_beta = await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: str(org_beta.id),
    }

    async with client:
        response = await client.get("/me/authorization", headers=headers)

    assert response.status_code == 403
    assert org_alpha.id != org_beta.id


@pytest.mark.asyncio
async def test_ambiguous_organization_selection_still_409(client, org_scope):
    await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    headers = {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}

    async with client:
        response = await client.get("/me/authorization", headers=headers)

    assert response.status_code == 409


# ---------------------------------------------------------- dependency behavior


@pytest.mark.asyncio
async def test_current_authorization_resolves_after_current_tenant(client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.ADMIN)
    headers = {
        "Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}",
        X_TENANT: str(org.id),
    }

    async with client:
        response = await client.get("/me/authorization", headers=headers)

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_require_capability_dependency_blocks_unauthorized(client, org_scope):
    # This test exercises the dependency factory via the /me/authorization endpoint
    # indirectly; a dedicated capability-gated route is not added in Phase 1D.1.
    # We verify the factory raises for missing capability by calling it directly.
    from app.api.deps import RequireCapability
    from app.core.rbac import AuthorizationContext, Capability, OrganizationRole

    authz = AuthorizationContext(
        organization_id=1, subject="sub", role=OrganizationRole.VIEWER
    )
    dep = RequireCapability(Capability.TICKET_WRITE)
    with pytest.raises(HTTPException):
        await dep(authz)


@pytest.mark.asyncio
async def test_require_capability_dependency_allows_authorized():
    from app.api.deps import RequireCapability
    from app.core.rbac import AuthorizationContext, Capability, OrganizationRole

    authz = AuthorizationContext(
        organization_id=1, subject="sub", role=OrganizationRole.AGENT
    )
    dep = RequireCapability(Capability.TICKET_WRITE)
    result = await dep(authz)
    assert result is authz
