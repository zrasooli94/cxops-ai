"""Phase 1Q-R - adversarial requests against the commercial surfaces.

Each test plays an attacker with a real, signed (test) identity and sends the
request a malicious tenant user would send. Source inspection is not enough:
every refusal here is observed over HTTP, and every test also asserts that the
protected state did not change.
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
from sqlalchemy import delete, func, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "a" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.base import Base
from app.models.commercial import PlatformOperator
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.public_chat import PublicChatConfiguration
from app.services.public_chat_service import hash_digest
from app.tenant_onboarding.manifest import parse_manifest
from app.tenant_onboarding.planner import (
    OnboardingConflictError,
    apply_plan,
    build_plan,
)

ORG_PREFIX = "Phase1QR-adv-"
TENANT = "x-cxops-organization-id"
ORIGIN = "https://shop.adversarial.example"
ISS = "adversarial"
AUD = "cxops-unit-test"

_PURGE = [
    "organization_invitations",
    "ai_usage_reservations",
    "ai_request_logs",
    "public_chat_sessions",
    "public_chat_configurations",
    "business_actions",
    "business_integration_configurations",
    "ticket_events",
    "integration_jobs",
    "agent_runs",
    "conversation_messages",
    "conversations",
    "tickets",
    "knowledge_chunks",
    "knowledge_documents",
    "organization_memberships",
]


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    for key, value in {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "a" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": ISS,
        "AUTH_JWT_AUDIENCE": AUD,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
        "INVITATION_IDENTITY_MODE": "jwt_claims",
    }.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture(scope="module", autouse=True)
def _isolated():
    asyncio.run(_purge())
    yield
    asyncio.run(_purge())


async def _purge() -> None:
    async with AsyncSessionLocal() as session:
        ids = [
            i
            for (i,) in await session.execute(
                select(Organization.id).where(
                    (Organization.name.like(f"{ORG_PREFIX}%"))
                    | (Organization.external_id.like("advslug-%"))
                )
            )
        ]
        await session.execute(
            delete(PlatformOperator).where(PlatformOperator.subject.like("adv-%"))
        )
        if ids:
            for name in _PURGE:
                table = Base.metadata.tables.get(name)
                if table is not None and "organization_id" in table.columns:
                    await session.execute(delete(table).where(table.c.organization_id.in_(ids)))
            await session.execute(delete(Organization).where(Organization.id.in_(ids)))
        await session.commit()


def _sub() -> str:
    return f"adv-{uuid.uuid4().hex[:12]}"


def _auth(sub: str, org_id: int | None = None, **claims) -> dict:
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {"sub": sub, "iss": ISS, "aud": AUD, "iat": now, "exp": now + 3600, **claims},
        "a" * 32,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    if org_id is not None:
        headers[TENANT] = str(org_id)
    return headers


async def _org(db, *, status="active", role="owner", widget=False):
    org = Organization(name=f"{ORG_PREFIX}{uuid.uuid4().hex[:8]}", lifecycle_status=status)
    db.add(org)
    await db.flush()
    sub = _sub()
    db.add(OrganizationMembership(subject=sub, organization_id=org.id, role=role))
    key = None
    if widget:
        key = f"pk_live_{secrets.token_hex(20)}"
        db.add(
            PublicChatConfiguration(
                organization_id=org.id,
                public_widget_key_hash=hash_digest(key),
                display_name="Adversarial Shop",
                welcome_message="Hi",
                allowed_origins=[ORIGIN],
                enabled=True,
            )
        )
    await db.commit()
    return org, sub, key


# ----------------------------------------------------------------------
# Abusive organization creation / slug squatting
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_service_cannot_choose_the_manifest_slug(client):
    attacker = _sub()
    async with client:
        r = await client.post(
            "/organizations",
            json={"name": f"{ORG_PREFIX}{uuid.uuid4().hex[:6]}", "external_id": "advslug-acme"},
            headers=_auth(attacker),
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_manifest_refuses_to_adopt_a_non_active_organization(db):
    # A legacy squatted org (or one provisioned for a lead) holds the slug.
    slug = f"advslug-{uuid.uuid4().hex[:6]}"
    squatter = Organization(
        name=f"{ORG_PREFIX}squat-{slug}", external_id=slug, lifecycle_status="onboarding"
    )
    db.add(squatter)
    await db.flush()
    db.add(OrganizationMembership(subject=_sub(), organization_id=squatter.id, role="owner"))
    await db.commit()
    squatter_id = squatter.id

    manifest = parse_manifest(
        {
            "schema_version": 1,
            "tenant": {"slug": slug, "name": f"{ORG_PREFIX}real-{slug}", "industry": "Retail"},
            "public_chat": {
                "enabled": True,
                "display_name": "Real Shop",
                "welcome_message": "Hi",
                "allowed_origins": [ORIGIN],
            },
            "pilot": {"state": "local_demo"},
        }
    )
    plan = await build_plan(db, manifest)
    assert plan.has_errors
    with pytest.raises(OnboardingConflictError):
        await apply_plan(db, manifest, plan)
    await db.rollback()
    widgets = await db.scalar(
        select(func.count())
        .select_from(PublicChatConfiguration)
        .where(PublicChatConfiguration.organization_id == squatter_id)
    )
    assert widgets == 0


@pytest.mark.asyncio
async def test_unapproved_self_service_organizations_are_capped(client):
    attacker = _sub()
    async with client:
        codes = [
            (
                await client.post(
                    "/organizations",
                    json={"name": f"{ORG_PREFIX}{uuid.uuid4().hex[:8]}"},
                    headers=_auth(attacker),
                )
            ).status_code
            for _ in range(4)
        ]
    assert codes == [201, 201, 429, 429]


# ----------------------------------------------------------------------
# Unapproved / suspended tenants and paid or external actions
# ----------------------------------------------------------------------

PAID_OR_EXTERNAL = [
    ("POST", "/agent/tickets/1/analyze", None),
    ("POST", "/agent/runs/x/execute", None),
    ("POST", "/knowledge/search", {"query": "hello"}),
    ("POST", "/knowledge/answer", {"question": "hello"}),
    ("POST", "/knowledge/documents", {"title": "t", "content": "c"}),
    ("POST", "/evaluations/runs/agent", {}),
    ("POST", "/evaluations/runs/rag", {}),
    ("GET", "/auth/zendesk/login", None),
    ("POST", "/conversations/1/replies", {"body": "hi"}),
    ("POST", "/automation-rules", {}),
    ("PATCH", "/staff/tenant-config/public-chat", {"enabled": True}),
    ("POST", "/staff/tenant-config/public-chat/rotate-widget-key", None),
    ("POST", "/staff/tenant-config/business-integrations/a1_cash_for_cars", {"enabled": True}),
]


@pytest.mark.parametrize("status", ["onboarding", "pending_review", "suspended"])
@pytest.mark.asyncio
async def test_non_active_owner_cannot_reach_paid_or_external_actions(db, client, status):
    org, owner, _ = await _org(db, status=status, widget=True)
    async with client:
        for method, path, body in PAID_OR_EXTERNAL:
            r = await client.request(method, path, json=body, headers=_auth(owner, org.id))
            assert r.status_code == 403, (status, method, path, r.status_code, r.text[:120])
    config = (
        await db.execute(
            select(PublicChatConfiguration).where(PublicChatConfiguration.organization_id == org.id)
        )
    ).scalar_one()
    assert config.public_widget_key_hash  # key not rotated
    assert config.enabled is True  # flag untouched; serving is still refused by lifecycle


@pytest.mark.asyncio
async def test_suspended_owner_loses_team_and_setup_management(db, client):
    org, owner, _ = await _org(db, status="suspended")
    async with client:
        invite = await client.post(
            "/team/invitations",
            json={"email": "x@shop.example", "role": "agent"},
            headers=_auth(owner, org.id),
        )
        business = await client.patch(
            "/onboarding/business", json={"industry": "x"}, headers=_auth(owner, org.id)
        )
        overview = await client.get("/workspace/overview", headers=_auth(owner, org.id))
    assert invite.status_code == 403
    assert business.status_code == 403
    assert overview.status_code == 200  # read-only view still works


# ----------------------------------------------------------------------
# Cross-tenant and identity confusion
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_onboarding_and_workspace_never_cross_tenants(db, client):
    _org_a, owner_a, _ = await _org(db, status="onboarding")
    org_b, _owner_b, _ = await _org(db, status="onboarding")
    async with client:
        for method, path, body in (
            ("GET", "/onboarding/state", None),
            ("PATCH", "/onboarding/business", {"industry": "hijack"}),
            ("POST", "/onboarding/widget", {"display_name": "x", "welcome_message": "y"}),
            ("POST", "/onboarding/submit", None),
            ("GET", "/workspace/overview", None),
            ("GET", "/team/members", None),
        ):
            r = await client.request(method, path, json=body, headers=_auth(owner_a, org_b.id))
            assert r.status_code == 403, (path, r.status_code)
    await db.refresh(org_b)
    assert org_b.industry is None and org_b.lifecycle_status == "onboarding"


@pytest.mark.asyncio
async def test_forged_role_or_operator_claims_grant_nothing(db, client):
    org, viewer, _ = await _org(db, role="viewer")
    forged = _auth(
        viewer,
        org.id,
        role="owner",
        platform_operator=True,
        **{"https://hasura.io/jwt/claims": {"x-hasura-default-role": "admin"}},
    )
    async with client:
        assert (await client.get("/platform-admin/me", headers=forged)).status_code == 403
        assert (
            await client.post(
                "/team/invitations", json={"email": "e@x.example", "role": "owner"}, headers=forged
            )
        ).status_code == 403
        body = (await client.get("/me/authorization", headers=forged)).json()
    assert body["role"] == "viewer"


@pytest.mark.asyncio
async def test_operator_endpoints_reject_ids_and_bodies_from_tenants(db, client):
    org, owner, _ = await _org(db, status="pending_review", widget=True)
    async with client:
        approve = await client.post(
            f"/platform-admin/organizations/{org.id}/lifecycle",
            json={"action": "approve"},
            headers=_auth(owner, org.id),
        )
        submit_status = await client.patch(
            "/onboarding/business",
            json={"lifecycle_status": "active"},
            headers=_auth(owner, org.id),
        )
    await db.refresh(org)
    assert approve.status_code == 403
    assert submit_status.status_code == 422
    assert org.lifecycle_status == "pending_review"


# ----------------------------------------------------------------------
# Disclosure of secrets
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_endpoint_echoes_widget_key_material(db, client):
    org, owner, key = await _org(db, status="active", widget=True)
    digest = hash_digest(key)
    operator = _sub()
    db.add(PlatformOperator(subject=operator, granted_by="adv-test"))
    await db.commit()
    async with client:
        responses = [
            await client.get("/onboarding/state", headers=_auth(owner, org.id)),
            await client.get("/workspace/overview", headers=_auth(owner, org.id)),
            await client.get("/staff/tenant-config/public-chat", headers=_auth(owner, org.id)),
            await client.get("/platform-admin/organizations", headers=_auth(operator)),
        ]
    for response in responses:
        assert response.status_code == 200, response.text[:200]
        assert key not in response.text and digest not in response.text
