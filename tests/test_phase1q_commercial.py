"""Phase 1Q - commercial onboarding: leads, operators, lifecycle, team.

Security properties under test (Workstream H):

- a tenant owner is never a platform operator; operator authority comes only
  from the ``platform_operators`` table (revocation is immediate)
- an organization that is not ``active`` cannot spend AI allowance, enable its
  widget, serve visitors, or reach external business actions
- invitations are single-use, unguessable, expiring, revocable and cannot be
  used to escalate privilege
- every tenant-scoped endpoint is bound to the caller's membership; naming
  another tenant (header or path id) yields 403/404 and changes nothing
- public lead capture is off by default, validated, rate-limited, and never
  reveals whether an address is already known
"""

import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, func, select, update

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "q" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-phase1q-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "cxops-unit-test"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import get_settings, reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import (
    AuthorizationContext,
    Capability,
    OrganizationRole,
    lifecycle_capability_mask,
)
from app.main import app
from app.models.base import Base
from app.models.commercial import OrganizationInvitation, PlatformOperator, SalesLead
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.public_chat import PublicChatConfiguration, PublicChatRateLimitBucket
from app.services.public_chat_service import hash_digest
from app.services.team_service import can_assign_role, can_manage_member
from app.services.tenant_config_service import (
    OrganizationNotApprovedError,
    TenantConfigService,
)

TENANT_HEADER = "x-cxops-organization-id"
ORG_PREFIX = "Phase1Q-test-"
LEAD_DOMAIN = "phase1q-lead.example"
ALLOWED_ORIGIN = "https://shop.phase1q.example"
X_ORIGIN = "x-embedding-origin"

_PURGE_TABLES = [
    "organization_invitations",
    "ai_usage_reservations",
    "ai_request_logs",
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
    "customer_identities",
    "customers",
    "knowledge_chunks",
    "knowledge_documents",
    "organization_memberships",
]


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "q" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": "test-phase1q-issuer",
        "AUTH_JWT_AUDIENCE": "cxops-unit-test",
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
        "PUBLIC_LEAD_CAPTURE_ENABLED": "false",
        "ONBOARDING_KNOWLEDGE_SETUP_ENABLED": "false",
    }
    for key, value in values.items():
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
def _namespace_isolated():
    asyncio.run(_purge())
    yield
    asyncio.run(_purge())


async def _purge() -> None:
    async with AsyncSessionLocal() as session:
        org_ids = [
            org_id
            for (org_id,) in await session.execute(
                select(Organization.id).where(Organization.name.like(f"{ORG_PREFIX}%"))
            )
        ]
        await session.execute(
            delete(SalesLead).where(SalesLead.email_normalized.like(f"%@{LEAD_DOMAIN}"))
        )
        await session.execute(
            delete(PlatformOperator).where(PlatformOperator.subject.like("p1q-%"))
        )
        await session.execute(
            delete(PublicChatRateLimitBucket).where(
                PublicChatRateLimitBucket.key_cache.like("lead_%")
            )
        )
        if org_ids:
            for table_name in _PURGE_TABLES:
                table = Base.metadata.tables.get(table_name)
                if table is not None and "organization_id" in table.columns:
                    await session.execute(
                        delete(table).where(table.c.organization_id.in_(org_ids))
                    )
            await session.execute(delete(Organization).where(Organization.id.in_(org_ids)))
        await session.commit()


def _token(sub: str, *, email: str | None = None) -> str:
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": sub,
        "iss": os.environ["AUTH_JWT_ISSUER"],
        "aud": os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + 3600,
        "iat": now,
    }
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, os.environ["AUTH_JWT_SECRET"], algorithm="HS256")


def _auth(sub: str, org_id: int | None = None, *, email: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token(sub, email=email)}"}
    if org_id is not None:
        headers[TENANT_HEADER] = str(org_id)
    return headers


def _subject() -> str:
    return f"p1q-{uuid.uuid4().hex[:12]}"


async def _org(
    db,
    *,
    status: str = "active",
    role: OrganizationRole = OrganizationRole.OWNER,
    widget: bool = False,
    widget_enabled: bool = True,
) -> tuple[Organization, str, str | None]:
    suffix = uuid.uuid4().hex[:8]
    org = Organization(
        name=f"{ORG_PREFIX}{suffix}",
        industry="automotive",
        lifecycle_status=status,
    )
    db.add(org)
    await db.flush()
    subject = _subject()
    db.add(OrganizationMembership(subject=subject, organization_id=org.id, role=role))
    key = None
    if widget:
        key = f"pk_live_{secrets.token_hex(20)}"
        db.add(
            PublicChatConfiguration(
                organization_id=org.id,
                public_widget_key_hash=hash_digest(key),
                display_name="Phase 1Q Shop",
                welcome_message="Hi there",
                allowed_origins=[ALLOWED_ORIGIN],
                enabled=widget_enabled,
            )
        )
    await db.commit()
    return org, subject, key


async def _add_member(db, org: Organization, role: OrganizationRole, email: str | None = None):
    subject = _subject()
    membership = OrganizationMembership(
        subject=subject, organization_id=org.id, role=role, email=email
    )
    db.add(membership)
    await db.commit()
    return subject, membership.id


async def _operator(db) -> str:
    subject = _subject()
    db.add(PlatformOperator(subject=subject, granted_by="test"))
    await db.commit()
    return subject


def _lead_payload(**overrides) -> dict:
    payload = {
        "company_name": "Acme Auto Repairs",
        "contact_name": "Sam Rivera",
        "email": f"sam+{uuid.uuid4().hex[:8]}@{LEAD_DOMAIN}",
        "website": "acme-auto.example.com",
        "industry": "automotive",
        "inquiry": "We get many booking questions after hours.",
        "consent": True,
    }
    payload.update(overrides)
    return payload


def _enable_leads(monkeypatch, **extra: str) -> None:
    monkeypatch.setenv("PUBLIC_LEAD_CAPTURE_ENABLED", "true")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


# ======================================================================
# RBAC lifecycle mask (pure)
# ======================================================================


def test_lifecycle_mask_withholds_spend_and_external_capabilities():
    pending = AuthorizationContext(
        organization_id=1,
        subject="s",
        role=OrganizationRole.OWNER,
        organization_status="onboarding",
    )
    for capability in (
        Capability.AGENT_RUN,
        Capability.AGENT_EXECUTE,
        Capability.AGENT_APPROVE,
        Capability.INTEGRATION_MANAGE,
        Capability.KNOWLEDGE_MANAGE,
        Capability.KNOWLEDGE_READ,
        Capability.EVALUATION_MANAGE,
        Capability.TICKET_WRITE,
        Capability.AUTOMATION_MANAGE,
    ):
        assert not pending.has(capability), capability
    assert pending.has(Capability.ORGANIZATION_MANAGE)
    assert pending.has(Capability.MEMBER_MANAGE)
    # The role itself is unchanged; approval restores everything.
    assert Capability.AGENT_RUN in pending.role_capabilities
    active = AuthorizationContext(
        organization_id=1, subject="s", role=OrganizationRole.OWNER
    )
    assert active.has(Capability.AGENT_RUN)


def test_unknown_and_suspended_status_fail_closed():
    assert lifecycle_capability_mask("bogus") == lifecycle_capability_mask("onboarding")
    suspended = lifecycle_capability_mask("suspended")
    assert Capability.MEMBER_MANAGE not in suspended
    assert Capability.ORGANIZATION_MANAGE not in suspended
    with_knowledge = lifecycle_capability_mask("onboarding", knowledge_setup_enabled=True)
    assert Capability.KNOWLEDGE_MANAGE in with_knowledge
    assert Capability.AGENT_RUN not in with_knowledge


def test_role_rank_rules():
    O, A, S, G, V = (
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.SUPERVISOR,
        OrganizationRole.AGENT,
        OrganizationRole.VIEWER,
    )
    assert can_assign_role(O, O) and can_assign_role(O, A)
    assert not can_assign_role(A, A) and not can_assign_role(A, O)
    assert can_assign_role(A, S) and can_assign_role(A, V)
    assert not can_manage_member(A, O) and not can_manage_member(A, A)
    assert can_manage_member(A, G)
    assert can_manage_member(S, G) and not can_manage_member(G, S)


# ======================================================================
# Self-service organizations start in onboarding
# ======================================================================


@pytest.mark.asyncio
async def test_self_service_organization_starts_onboarding_with_setup_capabilities(client):
    subject = _subject()
    name = f"{ORG_PREFIX}{uuid.uuid4().hex[:8]}"
    async with client:
        created = await client.post(
            "/organizations", json={"name": name}, headers=_auth(subject)
        )
        assert created.status_code == 201, created.text
        assert created.json()["lifecycle_status"] == "onboarding"
        org_id = created.json()["id"]

        authz = await client.get("/me/authorization", headers=_auth(subject, org_id))
        body = authz.json()
        assert body["role"] == "owner"
        assert body["organization_status"] == "onboarding"
        assert "agent.run" not in body["capabilities"]
        assert "integration.manage" not in body["capabilities"]
        assert "organization.manage" in body["capabilities"]

        # Paid / external routes are refused before any work happens.
        agent = await client.post(
            "/agent/tickets/1/analyze", headers=_auth(subject, org_id)
        )
        assert agent.status_code == 403
        search = await client.post(
            "/knowledge/search", json={"query": "hi"}, headers=_auth(subject, org_id)
        )
        assert search.status_code == 403
        integrations = await client.post(
            "/staff/tenant-config/business-integrations/a1_cash_for_cars",
            json={"enabled": True},
            headers=_auth(subject, org_id),
        )
        assert integrations.status_code == 403


@pytest.mark.asyncio
async def test_existing_active_organizations_keep_full_capabilities(db, client):
    org, subject, _ = await _org(db, status="active")
    async with client:
        body = (await client.get("/me/authorization", headers=_auth(subject, org.id))).json()
    assert body["organization_status"] == "active"
    assert "agent.run" in body["capabilities"]
    assert "integration.manage" in body["capabilities"]


# ======================================================================
# Public lead capture
# ======================================================================


@pytest.mark.asyncio
async def test_lead_capture_is_disabled_by_default(db, client):
    payload = _lead_payload()
    async with client:
        availability = await client.get("/public/leads/availability")
        response = await client.post("/public/leads", json=payload)
    assert availability.json()["accepting"] is False
    assert response.status_code == 503
    stored = await db.scalar(
        select(func.count())
        .select_from(SalesLead)
        .where(SalesLead.email_normalized == payload["email"].lower())
    )
    assert stored == 0


@pytest.mark.asyncio
async def test_lead_submission_validation(monkeypatch, client):
    _enable_leads(monkeypatch)
    async with client:
        for bad in (
            _lead_payload(consent=False),
            {k: v for k, v in _lead_payload().items() if k != "consent"},
            _lead_payload(email="not-an-email"),
            _lead_payload(industry="casino"),
            _lead_payload(company_name="   "),
            _lead_payload(website="ftp://example.com"),
            _lead_payload(inquiry="x" * 2001),
            _lead_payload(organization_id=1),
            _lead_payload(status="approved"),
        ):
            response = await client.post("/public/leads", json=bad)
            assert response.status_code == 422, (bad, response.text)


@pytest.mark.asyncio
async def test_lead_is_stored_as_new_and_duplicates_fold_without_overwrite(
    db, monkeypatch, client
):
    _enable_leads(monkeypatch)
    payload = _lead_payload()
    async with client:
        first = await client.post("/public/leads", json=payload)
        second = await client.post(
            "/public/leads",
            json={**payload, "company_name": "Attacker Rewrite", "email": payload["email"].upper()},
        )
    assert first.status_code == second.status_code == 202
    # Indistinguishable responses: no account / lead enumeration.
    assert first.json() == second.json()
    leads = list(
        (
            await db.execute(
                select(SalesLead).where(SalesLead.email_normalized == payload["email"].lower())
            )
        ).scalars()
    )
    assert len(leads) == 1
    lead = leads[0]
    assert lead.status == "new"
    assert lead.company_name == "Acme Auto Repairs"
    assert lead.submission_count == 2
    assert lead.website == "https://acme-auto.example.com"
    assert lead.consent_version


@pytest.mark.asyncio
async def test_lead_honeypot_is_silently_discarded(db, monkeypatch, client):
    _enable_leads(monkeypatch)
    payload = _lead_payload(company_fax="+1 555 0100")
    async with client:
        response = await client.post("/public/leads", json=payload)
    assert response.status_code == 202
    count = await db.scalar(
        select(func.count())
        .select_from(SalesLead)
        .where(SalesLead.email_normalized == payload["email"].lower())
    )
    assert count == 0


@pytest.mark.asyncio
async def test_lead_per_email_and_platform_rate_limits(db, monkeypatch, client):
    _enable_leads(monkeypatch, LEAD_SUBMISSIONS_PER_DAY_PER_EMAIL="2")
    payload = _lead_payload()
    async with client:
        codes = [
            (await client.post("/public/leads", json=payload)).status_code for _ in range(3)
        ]
    assert codes == [202, 202, 429]

    await db.execute(
        delete(PublicChatRateLimitBucket).where(
            PublicChatRateLimitBucket.key_cache.like("lead_platform:%")
        )
    )
    await db.commit()
    _enable_leads(monkeypatch, LEAD_SUBMISSIONS_PER_HOUR_PLATFORM="2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as fresh:
        codes = [
            (await fresh.post("/public/leads", json=_lead_payload())).status_code
            for _ in range(3)
        ]
    assert codes == [202, 202, 429]
    await db.execute(
        delete(PublicChatRateLimitBucket).where(
            PublicChatRateLimitBucket.key_cache.like("lead_%")
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_concurrent_duplicate_lead_submissions_create_one_lead(db, monkeypatch):
    _enable_leads(monkeypatch)
    payload = _lead_payload()

    async def submit():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            return (await c.post("/public/leads", json=payload)).status_code

    codes = await asyncio.gather(*(submit() for _ in range(3)))
    assert codes == [202, 202, 202]
    lead = (
        await db.execute(
            select(SalesLead).where(SalesLead.email_normalized == payload["email"].lower())
        )
    ).scalar_one()
    assert lead.submission_count == 3


# ======================================================================
# Platform operator authority
# ======================================================================


@pytest.mark.asyncio
async def test_tenant_owner_is_not_a_platform_operator(db, client):
    org, owner, _ = await _org(db)
    async with client:
        for method, path in (
            ("GET", "/platform-admin/me"),
            ("GET", "/platform-admin/leads"),
            ("GET", "/platform-admin/organizations"),
            ("GET", "/platform-admin/usage"),
            ("POST", f"/platform-admin/organizations/{org.id}/lifecycle"),
            ("PATCH", f"/platform-admin/organizations/{org.id}/entitlements"),
        ):
            json_body = (
                {"action": "approve"}
                if path.endswith("lifecycle")
                else ({"monthly_ai_turn_limit": 999999} if method == "PATCH" else None)
            )
            response = await client.request(
                method, path, json=json_body, headers=_auth(owner, org.id)
            )
            assert response.status_code == 403, (path, response.text)
        unauthenticated = await client.get("/platform-admin/leads")
        assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_operator_access_is_revoked_immediately(db, client):
    operator = await _operator(db)
    async with client:
        assert (await client.get("/platform-admin/me", headers=_auth(operator))).status_code == 200
        await db.execute(
            update(PlatformOperator)
            .where(PlatformOperator.subject == operator)
            .values(revoked_at=datetime.now(UTC), revoked_by="test")
        )
        await db.commit()
        assert (await client.get("/platform-admin/me", headers=_auth(operator))).status_code == 403


@pytest.mark.asyncio
async def test_operator_cannot_be_granted_by_email_claim(db, client):
    operator = await _operator(db)
    impostor = _subject()
    async with client:
        # Same email claim as an operator would carry, different verified sub.
        response = await client.get(
            "/platform-admin/me", headers=_auth(impostor, email=f"{operator}@cxops.example")
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_lead_lifecycle_and_provisioning(db, monkeypatch, client):
    _enable_leads(monkeypatch)
    operator = await _operator(db)
    payload = _lead_payload(company_name=f"{ORG_PREFIX}{uuid.uuid4().hex[:6]}")
    async with client:
        await client.post("/public/leads", json=payload)
        lead_id = (
            await db.execute(
                select(SalesLead.id).where(
                    SalesLead.email_normalized == payload["email"].lower()
                )
            )
        ).scalar_one()
        h = _auth(operator)

        early = await client.post(f"/platform-admin/leads/{lead_id}/provision", json={}, headers=h)
        assert early.status_code == 409  # not approved yet

        skip = await client.patch(
            f"/platform-admin/leads/{lead_id}", json={"status": "approved"}, headers=h
        )
        assert skip.status_code == 409  # new -> approved is not a valid transition

        for state in ("reviewing", "contacted", "approved"):
            r = await client.patch(
                f"/platform-admin/leads/{lead_id}",
                json={"status": state, "operator_notes": f"moved to {state}"},
                headers=h,
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == state

        listed = await client.get("/platform-admin/leads?status=approved", headers=h)
        assert any(item["id"] == lead_id for item in listed.json()["leads"])

        provisioned = await client.post(
            f"/platform-admin/leads/{lead_id}/provision", json={}, headers=h
        )
        assert provisioned.status_code == 201, provisioned.text
        body = provisioned.json()
        assert body["lifecycle_status"] == "onboarding"
        assert body["owner_invitation"]["role"] == "owner"
        assert body["owner_invitation"]["token"].startswith("inv_")

        again = await client.post(
            f"/platform-admin/leads/{lead_id}/provision", json={}, headers=h
        )
        assert again.status_code == 409

        # Approved is terminal.
        reopen = await client.patch(
            f"/platform-admin/leads/{lead_id}", json={"status": "reviewing"}, headers=h
        )
        assert reopen.status_code == 409

    org = await db.get(Organization, body["organization_id"])
    assert org is not None and org.lifecycle_status == "onboarding"
    # No member exists until the invitation is accepted.
    members = await db.scalar(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(OrganizationMembership.organization_id == org.id)
    )
    assert members == 0
    stored = (
        await db.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == org.id
            )
        )
    ).scalar_one()
    assert stored.token_hash != body["owner_invitation"]["token"]
    assert len(stored.token_hash) == 64


@pytest.mark.asyncio
async def test_concurrent_provisioning_creates_one_organization(db, monkeypatch):
    _enable_leads(monkeypatch)
    operator = await _operator(db)
    payload = _lead_payload(company_name=f"{ORG_PREFIX}{uuid.uuid4().hex[:6]}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/public/leads", json=payload)
    lead = (
        await db.execute(
            select(SalesLead).where(SalesLead.email_normalized == payload["email"].lower())
        )
    ).scalar_one()
    lead.status = "approved"
    await db.commit()

    async def provision():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return (
                await c.post(
                    f"/platform-admin/leads/{lead.id}/provision",
                    json={},
                    headers=_auth(operator),
                )
            ).status_code

    codes = sorted(await asyncio.gather(provision(), provision(), provision()))
    assert codes.count(201) == 1
    assert all(code in (201, 409) for code in codes)
    orgs = await db.scalar(
        select(func.count())
        .select_from(Organization)
        .where(Organization.name == payload["company_name"])
    )
    assert orgs == 1


@pytest.mark.asyncio
async def test_operator_lifecycle_and_entitlements(db, client):
    operator = await _operator(db)
    org, owner, _ = await _org(db, status="pending_review", widget=True, widget_enabled=False)
    async with client:
        h = _auth(operator)
        bad = await client.post(
            f"/platform-admin/organizations/{org.id}/lifecycle",
            json={"action": "reinstate"},
            headers=h,
        )
        assert bad.status_code == 409
        ok = await client.post(
            f"/platform-admin/organizations/{org.id}/lifecycle",
            json={"action": "approve"},
            headers=h,
        )
        assert ok.status_code == 200 and ok.json()["lifecycle_status"] == "active"

        ent = await client.patch(
            f"/platform-admin/organizations/{org.id}/entitlements",
            json={"plan_code": "pilot", "monthly_ai_turn_limit": 500, "monthly_ai_budget_usd": 2.5},
            headers=h,
        )
        assert ent.status_code == 200, ent.text

        # The tenant owner cannot change allowances through any tenant route.
        for path, body in (
            ("/staff/tenant-config/public-chat", {"monthly_ai_turn_limit": 10**6}),
            ("/onboarding/widget", {"monthly_ai_budget_usd": 9999}),
            ("/onboarding/business", {"plan_code": "enterprise"}),
        ):
            r = await client.patch(path, json=body, headers=_auth(owner, org.id))
            assert r.status_code == 422, (path, r.text)

        listing = await client.get("/platform-admin/organizations", headers=h)
        row = next(item for item in listing.json()["organizations"] if item["id"] == org.id)
        assert row["plan_code"] == "pilot"
        assert row["usage"]["turn_limit"] == 500
        assert row["usage"]["budget_usd"] == 2.5

        usage = await client.get("/platform-admin/usage", headers=h)
        assert usage.status_code == 200
        assert usage.json()["cost_basis"] == "estimated"

    config = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org.id
            )
        )
    ).scalar_one()
    await db.refresh(config)
    assert config.monthly_ai_turn_limit == 500


# ======================================================================
# Lifecycle gate on the widget and integrations
# ======================================================================


@pytest.mark.asyncio
async def test_unapproved_tenant_widget_never_serves_visitors(db, client):
    # Even if the enabled flag were set directly in the database.
    _org_row, _owner, key = await _org(
        db, status="onboarding", widget=True, widget_enabled=True
    )
    async with client:
        config = await client.get(f"/public/chat/config?key={key}")
        session = await client.post(
            "/public/chat/sessions",
            json={"public_widget_key": key},
            headers={X_ORIGIN: ALLOWED_ORIGIN},
        )
    assert config.status_code == 403
    assert session.status_code == 403


@pytest.mark.asyncio
async def test_suspension_and_disable_stop_open_sessions(db, client):
    org, _owner, key = await _org(db, status="active", widget=True)
    async with client:
        created = await client.post(
            "/public/chat/sessions",
            json={"public_widget_key": key},
            headers={X_ORIGIN: ALLOWED_ORIGIN},
        )
        assert created.status_code == 200, created.text
        token = created.json()["session"]["token"]
        headers = {"Authorization": f"Bearer {token}", X_ORIGIN: ALLOWED_ORIGIN}

        org.lifecycle_status = "suspended"
        await db.commit()
        suspended = await client.post(
            "/public/chat/messages",
            json={"client_message_id": "m1", "text": "hello"},
            headers=headers,
        )
        assert suspended.status_code == 403

        org.lifecycle_status = "active"
        await db.execute(
            update(PublicChatConfiguration)
            .where(PublicChatConfiguration.organization_id == org.id)
            .values(enabled=False)
        )
        await db.commit()
        disabled = await client.post(
            "/public/chat/messages",
            json={"client_message_id": "m2", "text": "hello"},
            headers=headers,
        )
        assert disabled.status_code == 403


@pytest.mark.asyncio
async def test_enabling_widget_or_integration_requires_approval(db):
    org, _owner, _key = await _org(db, status="onboarding", widget=True, widget_enabled=False)
    org_id = org.id
    with pytest.raises(OrganizationNotApprovedError):
        await TenantConfigService.update_public_chat(
            db, organization_id=org_id, changes={"enabled": True}, actor_subject="t"
        )
    await db.rollback()
    with pytest.raises(OrganizationNotApprovedError):
        await TenantConfigService.set_business_integration(
            db,
            organization_id=org_id,
            provider="a1_cash_for_cars",
            enabled=True,
            actor_subject="t",
        )
    await db.rollback()
    # Disabling is always allowed (it is the kill switch).
    result = await TenantConfigService.update_public_chat(
        db, organization_id=org_id, changes={"enabled": False}, actor_subject="t"
    )
    assert result["enabled"] is False


@pytest.mark.asyncio
async def test_widget_key_is_not_a_staff_credential(db, client):
    org, _owner, key = await _org(db, status="active", widget=True)
    async with client:
        for path in ("/workspace/overview", "/team/members", "/onboarding/state"):
            r = await client.get(
                path, headers={"Authorization": f"Bearer {key}", TENANT_HEADER: str(org.id)}
            )
            assert r.status_code == 401, (path, r.text)


# ======================================================================
# Setup wizard
# ======================================================================


@pytest.mark.asyncio
async def test_onboarding_wizard_flow(db, client):
    org, owner, _ = await _org(db, status="onboarding")
    h = _auth(owner, org.id)
    async with client:
        state = (await client.get("/onboarding/state", headers=h)).json()
        assert state["can_submit"] is False
        assert "Website domain" in state["missing_required"]

        early = await client.post("/onboarding/submit", headers=h)
        assert early.status_code == 409

        business = await client.patch(
            "/onboarding/business",
            json={"industry": "automotive", "website_url": "shop.phase1q.example"},
            headers=h,
        )
        assert business.status_code == 200, business.text
        assert business.json()["organization"]["website_url"] == "https://shop.phase1q.example"

        bad_origin = await client.post(
            "/onboarding/widget",
            json={
                "display_name": "Shop",
                "welcome_message": "Hi",
                "allowed_origins": ["https://*.phase1q.example"],
            },
            headers=h,
        )
        assert bad_origin.status_code == 400
        bad_theme = await client.post(
            "/onboarding/widget",
            json={"display_name": "Shop", "welcome_message": "Hi", "theme_token": "rispu"},
            headers=h,
        )
        assert bad_theme.status_code == 400

        created = await client.post(
            "/onboarding/widget",
            json={
                "display_name": "Phase 1Q Shop",
                "welcome_message": "Hello! How can we help?",
                "theme_token": "ocean",
                "allowed_origins": [ALLOWED_ORIGIN],
            },
            headers=h,
        )
        assert created.status_code == 201, created.text
        key = created.json()["public_widget_key"]
        assert key.startswith("pk_live_")

        again = await client.post(
            "/onboarding/widget",
            json={"display_name": "x", "welcome_message": "y"},
            headers=h,
        )
        assert again.status_code == 409
        assert "pk_live_" not in again.text

        enable = await client.patch("/onboarding/widget", json={"enabled": True}, headers=h)
        assert enable.status_code == 422  # not a field of the setup contract

        state = (await client.get("/onboarding/state", headers=h)).json()
        assert state["widget"]["enabled"] is False
        assert "public_widget_key" not in str(state) and key not in str(state)
        assert state["can_submit"] is True

        submitted = await client.post("/onboarding/submit", headers=h)
        assert submitted.status_code == 200
        assert submitted.json()["organization"]["lifecycle_status"] == "pending_review"

        locked = await client.patch("/onboarding/business", json={"industry": "x"}, headers=h)
        assert locked.status_code == 409

    stored = (
        await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org.id
            )
        )
    ).scalar_one()
    assert stored.public_widget_key_hash == hash_digest(key)
    assert stored.enabled is False


@pytest.mark.asyncio
async def test_concurrent_widget_creation_yields_one_configuration(db):
    org, owner, _ = await _org(db, status="onboarding")

    async def create():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.post(
                "/onboarding/widget",
                json={
                    "display_name": "Shop",
                    "welcome_message": "Hi",
                    "allowed_origins": [ALLOWED_ORIGIN],
                },
                headers=_auth(owner, org.id),
            )

    responses = await asyncio.gather(*(create() for _ in range(4)))
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1 and codes.count(409) == 3
    rows = await db.scalar(
        select(func.count())
        .select_from(PublicChatConfiguration)
        .where(PublicChatConfiguration.organization_id == org.id)
    )
    assert rows == 1


@pytest.mark.asyncio
async def test_agent_role_cannot_edit_onboarding(db, client):
    org, _owner, _ = await _org(db, status="onboarding")
    agent, _ = await _add_member(db, org, OrganizationRole.AGENT)
    async with client:
        r = await client.patch(
            "/onboarding/business", json={"industry": "x"}, headers=_auth(agent, org.id)
        )
    assert r.status_code == 403


# ======================================================================
# Team and invitations
# ======================================================================


async def _invite(client, inviter: str, org: Organization, email: str, role: str):
    return await client.post(
        "/team/invitations", json={"email": email, "role": role}, headers=_auth(inviter, org.id)
    )


@pytest.mark.asyncio
async def test_invitation_accept_is_single_use(db, client):
    org, owner, _ = await _org(db)
    email = f"agent-{uuid.uuid4().hex[:6]}@shop.example"
    invitee = _subject()
    other = _subject()
    async with client:
        issued = await _invite(client, owner, org, email, "agent")
        assert issued.status_code == 201, issued.text
        token = issued.json()["token"]

        listed = await client.get("/team/invitations", headers=_auth(owner, org.id))
        assert "token" not in listed.text and token not in listed.text

        preview = await client.post(
            "/invitations/preview", json={"token": token}, headers=_auth(invitee)
        )
        assert preview.status_code == 200
        assert preview.json()["role"] == "agent"

        accepted = await client.post(
            "/invitations/accept", json={"token": token}, headers=_auth(invitee, email=email)
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["organization_id"] == org.id

        reused = await client.post(
            "/invitations/accept", json={"token": token}, headers=_auth(other)
        )
        assert reused.status_code == 404

        authz = await client.get("/me/authorization", headers=_auth(invitee, org.id))
        assert authz.json()["role"] == "agent"


@pytest.mark.asyncio
async def test_invitation_tokens_cannot_be_guessed_expired_or_revoked(db, client):
    org, owner, _ = await _org(db)
    invitee = _subject()
    async with client:
        guessed = await client.post(
            "/invitations/accept",
            json={"token": "inv_" + secrets.token_urlsafe(32)},
            headers=_auth(invitee),
        )
        assert guessed.status_code == 404

        expiring = (await _invite(client, owner, org, "exp@shop.example", "viewer")).json()
        await db.execute(
            update(OrganizationInvitation)
            .where(OrganizationInvitation.id == expiring["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await db.commit()
        expired = await client.post(
            "/invitations/accept", json={"token": expiring["token"]}, headers=_auth(invitee)
        )
        assert expired.status_code == 404
        listed = (await client.get("/team/invitations", headers=_auth(owner, org.id))).json()
        assert any(
            i["id"] == expiring["id"] and i["status"] == "expired" for i in listed["invitations"]
        )

        revoking = (await _invite(client, owner, org, "rev@shop.example", "viewer")).json()
        revoked = await client.post(
            f"/team/invitations/{revoking['id']}/revoke", headers=_auth(owner, org.id)
        )
        assert revoked.status_code == 200
        use_revoked = await client.post(
            "/invitations/accept", json={"token": revoking["token"]}, headers=_auth(invitee)
        )
        assert use_revoked.status_code == 404

        # Re-inviting replaces the live invitation; the old token dies.
        first = (await _invite(client, owner, org, "again@shop.example", "agent")).json()
        second = (await _invite(client, owner, org, "again@shop.example", "agent")).json()
        old = await client.post(
            "/invitations/accept", json={"token": first["token"]}, headers=_auth(invitee)
        )
        assert old.status_code == 404
        new = await client.post(
            "/invitations/accept", json={"token": second["token"]}, headers=_auth(invitee)
        )
        assert new.status_code == 200


@pytest.mark.asyncio
async def test_invitation_bound_to_verified_email_when_present(db, client):
    org, owner, _ = await _org(db)
    async with client:
        token = (await _invite(client, owner, org, "right@shop.example", "agent")).json()["token"]
        wrong = await client.post(
            "/invitations/accept",
            json={"token": token},
            headers=_auth(_subject(), email="someone-else@shop.example"),
        )
        assert wrong.status_code == 403
        right = await client.post(
            "/invitations/accept",
            json={"token": token},
            headers=_auth(_subject(), email="RIGHT@shop.example"),
        )
        assert right.status_code == 200


@pytest.mark.asyncio
async def test_concurrent_acceptance_creates_one_membership(db):
    org, owner, _ = await _org(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        token = (await _invite(c, owner, org, "race@shop.example", "agent")).json()["token"]

    async def accept(subject):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return (
                await c.post("/invitations/accept", json={"token": token}, headers=_auth(subject))
            ).status_code

    codes = await asyncio.gather(*(accept(_subject()) for _ in range(4)))
    assert codes.count(200) == 1
    members = await db.scalar(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(OrganizationMembership.organization_id == org.id)
    )
    assert members == 2  # owner + exactly one invitee


@pytest.mark.asyncio
async def test_privilege_escalation_is_refused(db, client):
    org, owner, _ = await _org(db)
    admin, admin_mid = await _add_member(db, org, OrganizationRole.ADMIN)
    agent, agent_mid = await _add_member(db, org, OrganizationRole.AGENT)
    owner_mid = (
        await db.execute(
            select(OrganizationMembership.id).where(OrganizationMembership.subject == owner)
        )
    ).scalar_one()
    async with client:
        # Admin cannot mint admins or owners, nor touch the owner.
        assert (await _invite(client, admin, org, "x1@shop.example", "owner")).status_code == 403
        assert (await _invite(client, admin, org, "x2@shop.example", "admin")).status_code == 403
        assert (await _invite(client, admin, org, "x3@shop.example", "agent")).status_code == 201
        demote_owner = await client.patch(
            f"/team/members/{owner_mid}", json={"role": "viewer"}, headers=_auth(admin, org.id)
        )
        assert demote_owner.status_code == 403
        promote_agent = await client.patch(
            f"/team/members/{agent_mid}", json={"role": "admin"}, headers=_auth(admin, org.id)
        )
        assert promote_agent.status_code == 403
        # Nobody changes their own role.
        self_promote = await client.patch(
            f"/team/members/{admin_mid}", json={"role": "owner"}, headers=_auth(admin, org.id)
        )
        assert self_promote.status_code == 403
        # Agents have no member.manage at all.
        assert (await _invite(client, agent, org, "x4@shop.example", "viewer")).status_code == 403
        remove_other = await client.delete(
            f"/team/members/{admin_mid}", headers=_auth(agent, org.id)
        )
        assert remove_other.status_code == 403
        # The last owner can be neither demoted nor removed.
        last = await client.delete(f"/team/members/{owner_mid}", headers=_auth(owner, org.id))
        assert last.status_code == 409
        # Owner can manage everyone else.
        promote = await client.patch(
            f"/team/members/{agent_mid}", json={"role": "supervisor"}, headers=_auth(owner, org.id)
        )
        assert promote.status_code == 200
        # Any member may leave.
        leave = await client.delete(f"/team/members/{agent_mid}", headers=_auth(agent, org.id))
        assert leave.status_code == 204
        gone = await client.get("/me/authorization", headers=_auth(agent, org.id))
        assert gone.status_code == 403


@pytest.mark.asyncio
async def test_team_endpoints_are_tenant_bound(db, client):
    org_a, owner_a, _ = await _org(db)
    org_b, owner_b, _ = await _org(db)
    _victim, victim_mid = await _add_member(db, org_b, OrganizationRole.AGENT)
    async with client:
        b_invite = (await _invite(client, owner_b, org_b, "b@shop.example", "agent")).json()
        # Header naming B: not a member -> 403.
        assert (
            await client.get("/team/members", headers=_auth(owner_a, org_b.id))
        ).status_code == 403
        # B's ids addressed from A's tenant -> 404, nothing changes.
        r1 = await client.patch(
            f"/team/members/{victim_mid}", json={"role": "viewer"}, headers=_auth(owner_a, org_a.id)
        )
        r2 = await client.delete(f"/team/members/{victim_mid}", headers=_auth(owner_a, org_a.id))
        r3 = await client.post(
            f"/team/invitations/{b_invite['id']}/revoke", headers=_auth(owner_a, org_a.id)
        )
        assert (r1.status_code, r2.status_code, r3.status_code) == (404, 404, 404)
        members_a = (await client.get("/team/members", headers=_auth(owner_a, org_a.id))).json()
    assert all(m["id"] != victim_mid for m in members_a["members"])
    victim = await db.get(OrganizationMembership, victim_mid)
    await db.refresh(victim)
    assert victim.role == "agent"


@pytest.mark.asyncio
async def test_multi_company_user_switches_without_leakage(db, client):
    org_a, owner, _ = await _org(db, widget=True)
    org_b = Organization(name=f"{ORG_PREFIX}{uuid.uuid4().hex[:8]}", lifecycle_status="onboarding")
    db.add(org_b)
    await db.flush()
    db.add(OrganizationMembership(subject=owner, organization_id=org_b.id, role="viewer"))
    await db.commit()
    async with client:
        ambiguous = await client.get("/workspace/overview", headers=_auth(owner))
        assert ambiguous.status_code == 409
        a = (await client.get("/workspace/overview", headers=_auth(owner, org_a.id))).json()
        b = (await client.get("/workspace/overview", headers=_auth(owner, org_b.id))).json()
    assert a["organization"]["id"] == org_a.id and a["your_role"] == "owner"
    assert a["chatbot"]["configured"] is True
    assert b["organization"]["id"] == org_b.id and b["your_role"] == "viewer"
    assert b["organization"]["lifecycle_status"] == "onboarding"
    # Org A's chatbot never appears in org B's view.
    assert "Phase 1Q Shop" not in str(b)
    assert b["team"]["available"] is False  # viewer has no member.read


@pytest.mark.asyncio
async def test_workspace_sections_follow_capabilities(db, client):
    org, owner, _ = await _org(db, widget=True)
    agent, _ = await _add_member(db, org, OrganizationRole.AGENT)
    async with client:
        mine = (await client.get("/workspace/overview", headers=_auth(owner, org.id))).json()
        theirs = (await client.get("/workspace/overview", headers=_auth(agent, org.id))).json()
    assert mine["usage"]["available"] is True
    assert mine["usage"]["cost_basis"] == "estimated"
    assert "manage_team" in mine["actions"]
    assert theirs["usage"]["available"] is False
    assert theirs["team"]["available"] is False
    assert "manage_team" not in theirs["actions"]
    assert theirs["conversations"]["available"] is True


@pytest.mark.asyncio
async def test_knowledge_setup_flag_unlocks_only_knowledge(db, client, monkeypatch):
    monkeypatch.setenv("ONBOARDING_KNOWLEDGE_SETUP_ENABLED", "true")
    reset_settings_cache()
    assert get_settings().onboarding_knowledge_setup_enabled is True
    org, owner, _ = await _org(db, status="onboarding")
    async with client:
        body = (await client.get("/me/authorization", headers=_auth(owner, org.id))).json()
    assert "knowledge.manage" in body["capabilities"]
    assert "agent.run" not in body["capabilities"]


@pytest.mark.asyncio
async def test_ai_reservation_refused_for_unapproved_or_suspended_tenant(db):
    """The allowance reservation is the chokepoint before every public-chat
    model call: a non-active tenant gets no reservation (-> human handoff)."""
    from app.services.tenant_ai_allowance_service import TenantAIAllowanceService

    for status in ("onboarding", "pending_review", "suspended"):
        org, _owner, _key = await _org(db, status=status, widget=True)
        org_id = org.id
        decision = await TenantAIAllowanceService.reserve(
            db,
            organization_id=org_id,
            configuration=None,
            key=f"p1q-test:{org_id}:{uuid.uuid4().hex}",
        )
        assert decision.allowed is False, status
        assert decision.reason == "organization_not_active"
    active, _owner, _key = await _org(db, status="active", widget=True)
    allowed = await TenantAIAllowanceService.reserve(
        db,
        organization_id=active.id,
        configuration=None,
        key=f"p1q-test:{active.id}:{uuid.uuid4().hex}",
    )
    assert allowed.allowed is True
