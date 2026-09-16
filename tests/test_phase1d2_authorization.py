"""Phase 1D.2 — backend route & service authorization tests.

Tests that every human/JWT route enforces the approved capability matrix and
that the service-layer defense-in-depth guards raise the expected RBAC
denials. Machine-only paths are verified to carry no human-role gate.
"""

import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-phase1d2-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-phase1d2-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import (
    AuthorizationContext,
    MissingCapabilityError,
    OrganizationRole,
)
from app.main import app
from app.models.base import Base
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.services.agent_approval_service import AgentApprovalService
from app.services.embedding_service import embedding_service
from app.services.integration_job_service import (
    AgentExecutionQueueBlockedError,
    IntegrationJobService,
)
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.zendesk_oauth_service import ZendeskOAuthService

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-phase1d2-issuer"
TEST_AUDIENCE = "test-phase1d2-audience"
X_TENANT = "X-CXOps-Organization-ID"

USER_OWNER = "user-owner"
USER_ADMIN = "user-admin"
USER_SUPERVISOR = "user-supervisor"
USER_AGENT = "user-agent"
USER_VIEWER = "user-viewer"
USER_GUEST = "user-guest"


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": TEST_SECRET,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": TEST_ISSUER,
        "AUTH_JWT_AUDIENCE": TEST_AUDIENCE,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield


def _build_token(*, sub: str, **extra_claims) -> str:
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
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


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

    async def make(subject: str | None = None, role: OrganizationRole | None = None) -> Organization:
        org = Organization(name=f"phase1d2-test-{uuid.uuid4().hex[:10]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        if subject is not None and role is not None:
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
        tables = [
            "ticket_events",
            "agent_action_events",
            "knowledge_chunks",
            "ai_request_logs",
            "integration_jobs",
            "agent_runs",
            "knowledge_documents",
            "tickets",
            "customers",
            "automation_rules",
            "zendesk_oauth_tokens",
            "zendesk_oauth_states",
            "organization_memberships",
            "organizations",
        ]
        for table_name in tables:
            table = Base.metadata.tables.get(table_name)
            if table is not None and "organization_id" in table.columns:
                await db.execute(delete(table).where(table.c.organization_id.in_(org_ids)))
        await db.commit()


async def _add_member(db, org_id: int, subject: str, role: OrganizationRole) -> None:
    db.add(
        OrganizationMembership(
            subject=subject,
            organization_id=org_id,
            role=role,
        )
    )
    await db.commit()


def _headers(subject: str, org_id: int) -> dict:
    return {
        "Authorization": f"Bearer {_build_token(sub=subject)}",
        X_TENANT: str(org_id),
    }


# ---------------------------------------------------------------------- helpers


async def _allow(method: str, path: str, headers: dict, **kwargs):
    """A capability-allowed request must NOT be rejected with 403."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.request(method, path, headers=headers, **kwargs)
    assert response.status_code != 403, f"expected allow, got 403 for {method} {path}"
    return response


async def _deny(method: str, path: str, headers: dict, **kwargs):
    """A capability-denied request must fail closed with 403."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == 403, (
        f"expected 403, got {response.status_code} for {method} {path}"
    )
    return response


# ----------------------------------------------------------------- matrix tests


@pytest.mark.asyncio
async def test_customer_write_owner_allows_agent_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_AGENT, OrganizationRole.AGENT)
    body = {"name": "ACME", "email": "acme@example.com"}

    await _allow("POST", "/customers", _headers(USER_OWNER, org.id), json=body)
    await _deny("POST", "/customers", _headers(USER_AGENT, org.id), json=body)


@pytest.mark.asyncio
async def test_customer_read_allows_viewer(client, org_scope):
    org = await org_scope(subject=USER_VIEWER, role=OrganizationRole.VIEWER)
    await _allow("GET", "/customers", _headers(USER_VIEWER, org.id))


@pytest.mark.asyncio
async def test_customer_write_denies_viewer(client, org_scope):
    org = await org_scope(subject=USER_VIEWER, role=OrganizationRole.VIEWER)
    body = {"name": "ACME", "email": "acme@example.com"}
    await _deny("POST", "/customers", _headers(USER_VIEWER, org.id), json=body)


@pytest.mark.asyncio
async def test_ticket_write_owner_allows_viewer_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_VIEWER, OrganizationRole.VIEWER)
    body = {"subject": "Test ticket", "description": "Test description"}

    await _allow("POST", "/tickets", _headers(USER_OWNER, org.id), json=body)
    await _deny("POST", "/tickets", _headers(USER_VIEWER, org.id), json=body)


@pytest.mark.asyncio
async def test_ticket_read_allows_agent(client, org_scope):
    org = await org_scope(subject=USER_AGENT, role=OrganizationRole.AGENT)
    await _allow("GET", "/tickets", _headers(USER_AGENT, org.id))


@pytest.mark.asyncio
async def test_automation_manage_owner_allows_agent_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_AGENT, OrganizationRole.AGENT)
    body = {
        "name": "test-rule",
        "conditions": {},
        "actions": {},
    }

    await _allow("POST", "/automation-rules", _headers(USER_OWNER, org.id), json=body)
    await _deny("POST", "/automation-rules", _headers(USER_AGENT, org.id), json=body)


@pytest.mark.asyncio
async def test_automation_read_allows_viewer(client, org_scope):
    org = await org_scope(subject=USER_VIEWER, role=OrganizationRole.VIEWER)
    await _allow("GET", "/automation-rules", _headers(USER_VIEWER, org.id))


@pytest.mark.asyncio
async def test_organization_read_owner_allows_guest_denies(client, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _allow("GET", "/organizations", _headers(USER_OWNER, org.id))

    # A user with no membership at all hits the tenant gate (403).
    guest_org = await org_scope()
    await _deny("GET", "/organizations", _headers(USER_GUEST, guest_org.id))


@pytest.mark.asyncio
async def test_knowledge_manage_owner_allows_agent_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_AGENT, OrganizationRole.AGENT)
    body = {
        "title": "Test doc",
        "content": "Test content",
    }

    await _allow("POST", "/knowledge/documents", _headers(USER_OWNER, org.id), json=body)
    await _deny("POST", "/knowledge/documents", _headers(USER_AGENT, org.id), json=body)


@pytest.mark.asyncio
async def test_knowledge_read_allows_viewer(client, org_scope):
    org = await org_scope(subject=USER_VIEWER, role=OrganizationRole.VIEWER)
    await _allow("GET", "/knowledge/documents", _headers(USER_VIEWER, org.id))


@pytest.mark.asyncio
async def test_observability_read_owner_allows_agent_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_AGENT, OrganizationRole.AGENT)
    await _allow("GET", "/observability/ai/summary", _headers(USER_OWNER, org.id))
    await _deny("GET", "/observability/ai/summary", _headers(USER_AGENT, org.id))


@pytest.mark.asyncio
async def test_zendesk_read_owner_allows_viewer_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_VIEWER, OrganizationRole.VIEWER)
    await _allow("GET", "/zendesk/me", _headers(USER_OWNER, org.id))
    await _deny("GET", "/zendesk/me", _headers(USER_VIEWER, org.id))


@pytest.mark.asyncio
async def test_zendesk_ticket_comments_require_ticket_read(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    # All member roles currently hold TICKET_READ, so we verify gating by
    # comparing an allowed member against a user with no membership at all.
    await _allow("GET", "/zendesk/tickets/1/comments", _headers(USER_OWNER, org.id))

    guest_org = await org_scope()
    await _deny("GET", "/zendesk/tickets/1/comments", _headers(USER_GUEST, guest_org.id))


@pytest.mark.asyncio
async def test_zendesk_auth_login_owner_allows_supervisor_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_SUPERVISOR, OrganizationRole.SUPERVISOR)
    await _allow("GET", "/auth/zendesk/login", _headers(USER_OWNER, org.id))
    await _deny("GET", "/auth/zendesk/login", _headers(USER_SUPERVISOR, org.id))


@pytest.mark.asyncio
async def test_agent_run_owner_allows_viewer_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_VIEWER, OrganizationRole.VIEWER)
    await _allow("POST", "/agent/tickets/1/analyze", _headers(USER_OWNER, org.id))
    await _deny("POST", "/agent/tickets/1/analyze", _headers(USER_VIEWER, org.id))


@pytest.mark.asyncio
async def test_agent_approve_owner_allows_agent_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_AGENT, OrganizationRole.AGENT)
    await _allow("POST", "/agent/runs/000-approve-test/approve", _headers(USER_OWNER, org.id), json={"note": "ok"})
    await _deny("POST", "/agent/runs/000-approve-test/approve", _headers(USER_AGENT, org.id), json={"note": "ok"})


@pytest.mark.asyncio
async def test_agent_execute_owner_allows_agent_denies(client, db, org_scope):
    org = await org_scope(subject=USER_OWNER, role=OrganizationRole.OWNER)
    await _add_member(db, org.id, USER_AGENT, OrganizationRole.AGENT)
    await _allow("POST", "/agent/runs/000-execute-test/execute", _headers(USER_OWNER, org.id))
    await _deny("POST", "/agent/runs/000-execute-test/execute", _headers(USER_AGENT, org.id))


# ---------------------------------------------------------- cross-org role test


@pytest.mark.asyncio
async def test_cross_org_capabilities_enforced(client, org_scope):
    org_a = await org_scope(subject=USER_ADMIN, role=OrganizationRole.ADMIN)
    org_b = await org_scope(subject=USER_ADMIN, role=OrganizationRole.VIEWER)
    body = {"name": "ACME", "email": "acme@example.com"}

    # Admin in org A can write.
    await _allow("POST", "/customers", _headers(USER_ADMIN, org_a.id), json=body)

    # Same subject is viewer in org B, so write is denied.
    await _deny("POST", "/customers", _headers(USER_ADMIN, org_b.id), json=body)


# --------------------------------------------------------- service-layer guards


@pytest.mark.asyncio
async def test_agent_approval_service_requires_agent_approve():
    authz = AuthorizationContext(
        organization_id=1,
        subject="sub",
        role=OrganizationRole.AGENT,
    )
    with pytest.raises(MissingCapabilityError):
        await AgentApprovalService.approve(
            db=None,
            run_id="run-id",
            organization_id=1,
            note=None,
            authz=authz,
        )


@pytest.mark.asyncio
async def test_knowledge_ingestion_for_user_requires_knowledge_manage():
    authz = AuthorizationContext(
        organization_id=1,
        subject="sub",
        role=OrganizationRole.VIEWER,
    )
    with pytest.raises(MissingCapabilityError):
        await KnowledgeIngestionService.ingest_for_user(
            db=None,
            organization_id=1,
            title="t",
            content="c",
            source="manual",
            source_uri=None,
            metadata={},
            authz=authz,
        )


@pytest.mark.asyncio
async def test_knowledge_ingestion_primitive_allows_script_style_no_authz(
    db,
    org_scope,
    monkeypatch,
):
    """The low-level ingest primitive must not require a human AuthorizationContext."""

    async def _fake_embed_documents(texts: list[str]) -> list[list[float]]:
        return [[float(i)] * 1536 for i in range(len(texts))]

    monkeypatch.setattr(
        embedding_service,
        "embed_documents",
        _fake_embed_documents,
    )

    org = await org_scope()
    result = await KnowledgeIngestionService.ingest(
        db=db,
        organization_id=org.id,
        title="Script-style doc",
        content="Script-style content for the regression test.",
        source="seed-script",
        source_uri=None,
        metadata={"seeded": True},
    )
    assert result["duplicate"] is False
    assert result["document_id"] is not None


@pytest.mark.asyncio
async def test_zendesk_oauth_service_requires_integration_manage():
    authz = AuthorizationContext(
        organization_id=1,
        subject="sub",
        role=OrganizationRole.AGENT,
    )
    with pytest.raises(MissingCapabilityError):
        await ZendeskOAuthService.create_state(
            db=None,
            organization_id=1,
            subject="sub",
            authz=authz,
        )


# ---------------------------------------------------------- machine-path checks


@pytest.mark.asyncio
async def test_zendesk_callback_is_public_no_role_gate(client):
    """OAuth provider redirect must not require a human JWT role."""
    async with client:
        response = await client.get("/auth/zendesk/callback")
    assert response.status_code in {200, 307, 400, 422}
    assert response.status_code not in {401, 403}


@pytest.mark.asyncio
async def test_ticket_event_webhook_is_not_role_gated(client):
    """Signed webhook must fail on signature, not on missing human role."""
    async with client:
        response = await client.post("/webhooks/ticket-events", json={})
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_agent_execution_enqueue_needs_no_human_authz(db, org_scope):
    """IntegrationJobService.enqueue_agent_execution is a shared machine primitive.

    The human HTTP execute route requires AGENT_EXECUTE, but the internal
    queue primitive itself must remain capability-neutral so automation
    workflows can enqueue approved runs without a fabricated AuthorizationContext.
    """
    org = await org_scope()
    with pytest.raises(AgentExecutionQueueBlockedError):
        await IntegrationJobService.enqueue_agent_execution(
            db=db,
            run_id="missing-run-id",
            organization_id=org.id,
        )


@pytest.mark.asyncio
async def test_zendesk_user_capability_separation(client, db, org_scope):
    """/zendesk/me requires integration.read; /zendesk/users/{id} requires customer.read."""
    org = await org_scope(subject=USER_VIEWER, role=OrganizationRole.VIEWER)

    # VIEWER lacks integration.read, so /me is forbidden.
    await _deny("GET", "/zendesk/me", _headers(USER_VIEWER, org.id))

    # VIEWER has customer.read, so /users/{id} is not forbidden by RBAC.
    response = await _allow("GET", "/zendesk/users/123", _headers(USER_VIEWER, org.id))
    # The route may still fail upstream (Zendesk unavailable in tests), but it
    # must not be a 403 from the capability gate.
    assert response.status_code != 403
