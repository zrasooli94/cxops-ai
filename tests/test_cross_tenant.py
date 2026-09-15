"""Cross-tenant security tests for customers, tickets, organizations, ticket events.

Tests A–O per Phase 1C.2 spec. Uses synthetic subjects (user-alpha, user-beta)
and two organizations (Org A, Org B) with no shared memberships.
"""

import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, func, select
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
from app.main import app
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-tenant-issuer"
TEST_AUDIENCE = "test-tenant-audience"

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


def _build_token(*, sub: str, secret: str = TEST_SECRET) -> str:
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
def make_client():
    """Factory fixture that returns a fresh AsyncClient for each call."""
    def _make():
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    return _make


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")




@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def two_orgs(db):
    """Create two organizations: Org A (alpha's), Org B (beta's)."""
    # Clean slate: remove ALL memberships for test subjects before starting
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
    # Keep plain PKs: the teardown rollback expires ORM attributes, and a lazy
    # reload then raises MissingGreenlet outside an awaited SQLAlchemy call.
    org_a_id = org_a.id
    org_b_id = org_b.id

    # Grant alpha membership in Org A, beta in Org B
    db.add_all([
        OrganizationMembership(subject=USER_ALPHA, organization_id=org_a_id),
        OrganizationMembership(subject=USER_BETA, organization_id=org_b_id),
    ])
    await db.commit()

    try:
        yield org_a, org_b
    finally:
        # Reset any incomplete/aborted transaction before cleanup runs.
        await db.rollback()
        # Cleanup - remove ALL memberships for test subjects, then orgs
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.subject.in_([USER_ALPHA, USER_BETA])
            )
        )
        await db.execute(
            delete(Organization).where(Organization.id.in_([org_a_id, org_b_id]))
        )
        await db.commit()


@pytest_asyncio.fixture
async def tenant_data(db, two_orgs):
    """Create customers and tickets in both organizations."""
    org_a, org_b = two_orgs

    # Unique emails to avoid unique constraint conflicts across test runs
    unique_alpha = f"alpha-cust-{uuid.uuid4().hex[:8]}@example.com"
    unique_beta = f"beta-cust-{uuid.uuid4().hex[:8]}@example.com"

    # Alpha's customer + ticket
    cust_a = Customer(
        name="Alpha Customer",
        email=unique_alpha,
        organization_id=org_a.id,
    )
    # Beta's customer + ticket
    cust_b = Customer(
        name="Beta Customer",
        email=unique_beta,
        organization_id=org_b.id,
    )
    db.add_all([cust_a, cust_b])
    await db.flush()

    ticket_a = Ticket(
        subject="Alpha Ticket",
        description="Alpha's ticket",
        organization_id=org_a.id,
        customer_id=cust_a.id,
    )
    ticket_b = Ticket(
        subject="Beta Ticket",
        description="Beta's ticket",
        organization_id=org_b.id,
        customer_id=cust_b.id,
    )
    db.add_all([ticket_a, ticket_b])
    await db.flush()

    event_a = TicketEvent(
        event_key=f"event-alpha-{uuid.uuid4().hex}",
        event_type="ticket_created",
        source="test",
        ticket_id=ticket_a.id,
        payload={"test": "alpha"},
    )
    event_b = TicketEvent(
        event_key=f"event-beta-{uuid.uuid4().hex}",
        event_type="ticket_created",
        source="test",
        ticket_id=ticket_b.id,
        payload={"test": "beta"},
    )
    db.add_all([event_a, event_b])
    await db.commit()

    # Keep plain IDs/keys: the teardown rollback expires ORM attributes, and a
    # lazy reload then raises MissingGreenlet outside an awaited SQLAlchemy call.
    cust_a_id = cust_a.id
    cust_b_id = cust_b.id
    ticket_a_id = ticket_a.id
    ticket_b_id = ticket_b.id
    event_a_key = event_a.event_key
    event_b_key = event_b.event_key

    yield {
        "org_a": org_a,
        "org_b": org_b,
        "cust_a": cust_a,
        "cust_b": cust_b,
        "ticket_a": ticket_a,
        "ticket_b": ticket_b,
        "event_a": event_a,
        "event_b": event_b,
        "email_alpha": unique_alpha,
        "email_beta": unique_beta,
    }

    # Cleanup in correct FK order: ticket_events → tickets → customers
    # Rollback first to reset any incomplete/aborted transaction from the test.
    await db.rollback()
    await db.execute(
        delete(TicketEvent).where(
            TicketEvent.event_key.in_([event_a_key, event_b_key])
        )
    )
    await db.execute(
        delete(Ticket).where(Ticket.id.in_([ticket_a_id, ticket_b_id]))
    )
    await db.execute(
        delete(Customer).where(Customer.id.in_([cust_a_id, cust_b_id]))
    )
    await db.commit()


# Convenience headers
def _h_alpha():
    return {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}


def _h_beta():
    return {"Authorization": f"Bearer {_build_token(sub=USER_BETA)}"}


# ===================================================================
# A. user-alpha lists only Org A customers
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_lists_only_org_a_customers(make_client, tenant_data):
    async with make_client() as client:
        r = await client.get("/customers", headers=_h_alpha())
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["email"] == tenant_data["email_alpha"]
    assert body[0]["organization_id"] == tenant_data["org_a"].id


# ===================================================================
# B. user-alpha cannot get Org B customer by ID
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_cannot_get_org_b_customer(make_client, tenant_data):
    async with make_client() as client:
        r = await client.get(f"/customers/{tenant_data['cust_b'].id}", headers=_h_alpha())
    # 404 (not 403) to avoid resource enumeration
    assert r.status_code == 404


# ===================================================================
# C. user-alpha cannot update Org B customer
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_cannot_update_org_b_customer(make_client, tenant_data):
    async with make_client() as client:
        r = await client.patch(
            f"/customers/{tenant_data['cust_b'].id}",
            headers=_h_alpha(),
            json={"name": "Hacked"},
        )
    assert r.status_code == 404  # not found in tenant scope


# ===================================================================
# D. user-alpha cannot create customer assigned to Org B
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_cannot_create_customer_with_org_b(make_client, tenant_data, db):
    async with make_client() as client:
        r = await client.post(
            "/customers",
            headers=_h_alpha(),
            json={
                "name": "Evil Customer",
                "email": f"evil-{uuid.uuid4().hex[:8]}@example.com",
                # No organization_id in schema - should ignore
            },
        )
    # Should create in alpha's org (org_a)
    assert r.status_code == 201
    body = r.json()
    assert body["organization_id"] == tenant_data["org_a"].id
    assert body["email"].startswith("evil-")

    # Cleanup the created customer so two_orgs teardown can delete the organization
    evil_cust_id = body["id"]
    await db.rollback()  # ensure clean session
    await db.execute(delete(Customer).where(Customer.id == evil_cust_id))
    await db.commit()


# ===================================================================
# E. user-alpha lists only Org A tickets
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_lists_only_org_a_tickets(make_client, tenant_data):
    async with make_client() as client:
        r = await client.get("/tickets", headers=_h_alpha())
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["subject"] == "Alpha Ticket"
    assert body[0]["organization_id"] == tenant_data["org_a"].id


# ===================================================================
# F. user-alpha cannot get Org B ticket by guessed ID
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_cannot_get_org_b_ticket(make_client, tenant_data):
    async with make_client() as client:
        r = await client.get(f"/tickets/{tenant_data['ticket_b'].id}", headers=_h_alpha())
    assert r.status_code == 404


# ===================================================================
# G. user-alpha cannot update Org B ticket
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_cannot_update_org_b_ticket(make_client, tenant_data):
    async with make_client() as client:
        r = await client.patch(
            f"/tickets/{tenant_data['ticket_b'].id}",
            headers=_h_alpha(),
            json={"subject": "Hacked"},
        )
    assert r.status_code == 404


# ===================================================================
# H. user-alpha cannot access Org B ticket events
# ===================================================================
@pytest.mark.asyncio
async def test_alpha_cannot_access_org_b_ticket_events(db, tenant_data):
    from app.repositories.ticket_event_repository import TicketEventRepository

    # Org A scope sees only Org A ticket events; Org B events are absent.
    org_a_events = await TicketEventRepository.list_for_tenant(
        db, tenant_data["org_a"].id
    )
    org_a_keys = {e.event_key for e in org_a_events}
    assert tenant_data["event_a"].event_key in org_a_keys
    assert tenant_data["event_b"].event_key not in org_a_keys

    # The join-based tenant predicate is load-bearing: removing the
    # Ticket.organization_id filter would return both organizations' events.
    org_b_events = await TicketEventRepository.list_for_tenant(
        db, tenant_data["org_b"].id
    )
    assert org_b_events
    assert all(e.ticket_id == tenant_data["ticket_b"].id for e in org_b_events)


# ===================================================================
# I. forged X-CXOps-Organization-ID Org B -> 403 before data access
# ===================================================================
@pytest.mark.asyncio
async def test_forged_selector_returns_403(make_client, tenant_data):
    headers = {**_h_alpha(), X_TENANT: str(tenant_data["org_b"].id)}
    async with make_client() as client:
        r = await client.get("/me/tenant", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_forged_selector_on_customers_403(make_client, tenant_data):
    headers = {**_h_alpha(), X_TENANT: str(tenant_data["org_b"].id)}
    async with make_client() as client:
        r = await client.get("/customers", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_forged_selector_on_tickets_403(make_client, tenant_data):
    headers = {**_h_alpha(), X_TENANT: str(tenant_data["org_b"].id)}
    async with make_client() as client:
        r = await client.get("/tickets", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_forged_selector_on_organizations_403(make_client, tenant_data):
    headers = {**_h_alpha(), X_TENANT: str(tenant_data["org_b"].id)}
    async with make_client() as client:
        r = await client.get("/organizations", headers=headers)
    assert r.status_code == 403


# ===================================================================
# J. ticket creation always receives tenant organization ownership
# ===================================================================
@pytest.mark.asyncio
async def test_ticket_creation_gets_tenant_org(make_client, tenant_data, db):
    async with make_client() as client:
        r = await client.post(
            "/tickets",
            headers=_h_alpha(),
            json={
                "subject": "New Ticket",
                "description": "Created in alpha's tenant",
                "priority": "normal",
                "source": "api",
            },
        )
    assert r.status_code == 201
    body = r.json()
    assert body["organization_id"] == tenant_data["org_a"].id
    assert body["subject"] == "New Ticket"

    # Cleanup the created ticket so tenant_data teardown can delete the organization
    new_ticket_id = body["id"]
    await db.rollback()  # ensure clean session
    await db.execute(delete(Ticket).where(Ticket.id == new_ticket_id))
    await db.commit()


# ===================================================================
# K. customer ownership mismatch on ticket creation is rejected
# ===================================================================
@pytest.mark.asyncio
async def test_ticket_creation_rejects_cross_tenant_customer(make_client, tenant_data):
    # Try to create ticket with beta's customer_id in alpha's tenant
    async with make_client() as client:
        r = await client.post(
            "/tickets",
            headers=_h_alpha(),
            json={
                "subject": "Cross Tenant",
                "description": "Should fail",
                "priority": "normal",
                "source": "api",
                "customer_id": tenant_data["cust_b"].id,  # Beta's customer
            },
        )
    # Should 404 (customer not found in alpha's tenant)
    assert r.status_code == 404


# ===================================================================
# L. unauthenticated routes still 401
# ===================================================================
@pytest.mark.asyncio
async def test_unauthenticated_customers_401(client):
    r = await client.get("/customers")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_tickets_401(client):
    r = await client.get("/tickets")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_organizations_401(client):
    r = await client.get("/organizations")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_me_tenant_401(client):
    r = await client.get("/me/tenant")
    assert r.status_code == 401


# ===================================================================
# M. no-membership routes 403
# ===================================================================
@pytest.mark.asyncio
async def test_no_membership_403(make_client, db, two_orgs):
    """User with no memberships gets 403 on tenant endpoints."""
    # Create a third user with no memberships
    headers = {"Authorization": f"Bearer {_build_token(sub='user-gamma')}"}

    async with make_client() as client:
        for path in ["/customers", "/tickets", "/organizations", "/me/tenant"]:
            r = await client.get(path, headers=headers)
            assert r.status_code == 403, f"{path} should 403 for no-membership user"


# ===================================================================
# N. multi-membership without selector -> 409
# ===================================================================
@pytest.mark.asyncio
async def test_multi_membership_without_selector_409(make_client, db, two_orgs):
    """Grant alpha membership in BOTH orgs, then call without selector."""
    _org_a, org_b = two_orgs
    # Add alpha to org_b as well
    db.add(OrganizationMembership(subject=USER_ALPHA, organization_id=org_b.id))
    await db.commit()

    headers = _h_alpha()
    async with make_client() as client:
        for path in ["/customers", "/tickets", "/organizations", "/me/tenant"]:
            r = await client.get(path, headers=headers)
            assert r.status_code == 409, f"{path} should 409 for multi-membership without selector"

    # Clean up the extra membership so subsequent tests see clean state
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.subject == USER_ALPHA,
            OrganizationMembership.organization_id == org_b.id,
        )
    )
    await db.commit()


# ===================================================================
# O. valid selector scopes data to exactly the selected organization
# ===================================================================
@pytest.mark.asyncio
async def test_valid_selector_scopes_data(make_client, db, two_orgs, tenant_data):
    _org_a, org_b = two_orgs
    # Ensure alpha has membership in org_b for this test
    db.add(OrganizationMembership(subject=USER_ALPHA, organization_id=org_b.id))
    await db.commit()

    headers = {**_h_alpha(), X_TENANT: str(org_b.id)}

    async with make_client() as client:
        r1 = await client.get("/customers", headers=headers)
        assert r1.status_code == 200
        body = r1.json()
        assert len(body) == 1
        assert body[0]["email"] == tenant_data["email_beta"]
        assert body[0]["organization_id"] == org_b.id

        r2 = await client.get("/tickets", headers=headers)
        assert r2.status_code == 200
        body = r2.json()
        assert len(body) == 1
        assert body[0]["subject"] == "Beta Ticket"
        assert body[0]["organization_id"] == org_b.id

    # Cleanup
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.subject == USER_ALPHA,
            OrganizationMembership.organization_id == org_b.id,
        )
    )
    await db.commit()


# ===================================================================
# Additional: organization endpoints respect membership
# ===================================================================
@pytest.mark.asyncio
async def test_organization_list_membership_scoped(make_client, db, two_orgs):
    org_a, org_b = two_orgs

    # Alpha only in org_a initially
    async with make_client() as client:
        r1 = await client.get("/organizations", headers=_h_alpha())
    assert r1.status_code == 200
    body = r1.json()
    assert len(body) == 1
    assert body[0]["id"] == org_a.id

    # Beta only in org_b
    async with make_client() as client:
        r2 = await client.get("/organizations", headers=_h_beta())
    assert r2.status_code == 200
    body = r2.json()
    assert len(body) == 1
    assert body[0]["id"] == org_b.id


@pytest.mark.asyncio
async def test_customer_repo_internal_vs_tenant(db, two_orgs, tenant_data):
    org_a, org_b = two_orgs
    from app.repositories.customer_repository import CustomerRepository

    # Internal global get_by_id finds beta's customer
    cust = await CustomerRepository.get_by_id_unscoped(db, tenant_data["cust_b"].id)
    assert cust is not None
    assert cust.organization_id == org_b.id

    # Tenant-safe get_by_id_for_tenant does NOT find it for alpha
    cust = await CustomerRepository.get_by_id_for_tenant(db, tenant_data["cust_b"].id, org_a.id)
    assert cust is None

    # Tenant-safe list_for_tenant returns only alpha's
    custs = await CustomerRepository.list_for_tenant(db, org_a.id)
    assert len(custs) == 1
    assert custs[0].email == tenant_data["email_alpha"]


@pytest.mark.asyncio
async def test_organization_get_requires_membership(make_client, db, two_orgs):
    org_a, org_b = two_orgs

    # Alpha can get org_a
    async with make_client() as client:
        r = await client.get(f"/organizations/{org_a.id}", headers=_h_alpha())
    assert r.status_code == 200

    # Alpha cannot get org_b (no membership)
    async with make_client() as client:
        r = await client.get(f"/organizations/{org_b.id}", headers=_h_alpha())
    assert r.status_code == 404


# ===================================================================
# Repository-level safety: internal methods not used in tenant routes
# ===================================================================
@pytest.mark.asyncio
async def test_ticket_repo_internal_vs_tenant(db, two_orgs, tenant_data):
    org_a, org_b = two_orgs
    from app.repositories.ticket_repository import TicketRepository

    ticket = await TicketRepository.get_by_id_unscoped(db, tenant_data["ticket_b"].id)
    assert ticket is not None
    assert ticket.organization_id == org_b.id

    ticket = await TicketRepository.get_by_id_for_tenant(db, tenant_data["ticket_b"].id, org_a.id)
    assert ticket is None


# ===================================================================
# Repository-level safety: update_for_tenant validates tenant
# ===================================================================
@pytest.mark.asyncio
async def test_ticket_update_for_tenant_validates_org(db, tenant_data):
    from app.repositories.ticket_repository import TicketRepository

    ticket = await TicketRepository.get_by_id_unscoped(db, tenant_data["ticket_a"].id)
    with pytest.raises(ValueError):
        await TicketRepository.update_for_tenant(
            db, ticket, {"subject": "Hacked"}, organization_id=tenant_data["org_b"].id
        )


# ===================================================================
# Lifecycle: an expected DB constraint failure must not brick the session
# ===================================================================
@pytest.mark.asyncio
async def test_session_reusable_after_expected_db_error(db):
    """Expected constraint failure -> rollback -> session usable again."""
    org_name = f"dup-org-{uuid.uuid4().hex[:8]}"
    org = Organization(name=org_name)
    db.add(org)
    await db.flush()
    org_id = org.id

    # Deliberately violate the organizations.name unique constraint.
    db.add(Organization(name=org_name))
    with pytest.raises(IntegrityError):
        await db.flush()

    # Repair the aborted transaction; the session must work again.
    await db.rollback()

    count = (
        await db.execute(select(func.count()).select_from(Organization))
    ).scalar_one()
    assert isinstance(count, int)

    await db.execute(delete(Organization).where(Organization.id == org_id))
    await db.commit()


# ===================================================================
# M2: PATCH can no longer null NOT NULL columns (was HTTP 500)
# ===================================================================
@pytest.mark.asyncio
async def test_customer_update_null_required_field_422(make_client, tenant_data):
    async with make_client() as client:
        r = await client.patch(
            f"/customers/{tenant_data['cust_a'].id}",
            headers=_h_alpha(),
            json={"email": None},
        )
    assert r.status_code == 422

    async with make_client() as client:
        r = await client.patch(
            f"/customers/{tenant_data['cust_a'].id}",
            headers=_h_alpha(),
            json={"name": None},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ticket_update_null_required_field_422(make_client, tenant_data):
    async with make_client() as client:
        r = await client.patch(
            f"/tickets/{tenant_data['ticket_a'].id}",
            headers=_h_alpha(),
            json={"subject": None},
        )
    assert r.status_code == 422

    async with make_client() as client:
        r = await client.patch(
            f"/tickets/{tenant_data['ticket_a'].id}",
            headers=_h_alpha(),
            json={"description": None},
        )
    assert r.status_code == 422


# ===================================================================
# M3: PATCH customer_id null = explicit unlink; ticket keeps tenant org
# ===================================================================
@pytest.mark.asyncio
async def test_ticket_customer_unlink_keeps_tenant_ownership(make_client, tenant_data):
    async with make_client() as client:
        r = await client.patch(
            f"/tickets/{tenant_data['ticket_a'].id}",
            headers=_h_alpha(),
            json={"customer_id": None},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] is None
    assert body["organization_id"] == tenant_data["org_a"].id
    assert body["id"] == tenant_data["ticket_a"].id


# ===================================================================
# H1 remediation: tenant-facing Zendesk sync is tenant-scoped
# ===================================================================
def _fake_zendesk_ticket(
    subject="Zendesk Synced",
    requester_id=None,
    description="Synced from Zendesk",
):
    async def _get_ticket(db, ticket_id):
        return {
            "ticket": {
                "id": ticket_id,
                "subject": subject,
                "description": description,
                "requester_id": requester_id,
                "status": "open",
                "priority": "high",
            }
        }

    return _get_ticket


def _fake_zendesk_user(email, name="Zendesk Requester"):
    async def _get_user(db, user_id):
        return {"user": {"id": user_id, "email": email, "name": name}}

    return _get_user


def _zendesk_external_id() -> int:
    return (int(uuid.uuid4().hex[:12], 16) % 900_000_000) + 100_000_000


@pytest.mark.asyncio
async def test_tenant_sync_cannot_update_foreign_ticket(
    monkeypatch, make_client, db, tenant_data
):
    from app.integrations.zendesk.client import zendesk_client
    from app.repositories.ticket_repository import TicketRepository

    zid = _zendesk_external_id()
    # Org B owns the local record for this Zendesk id.
    b_ticket = Ticket(
        subject="Org B owned",
        description="Belongs to B",
        status="new",
        priority="normal",
        organization_id=tenant_data["org_b"].id,
        external_id=str(zid),
    )
    db.add(b_ticket)
    await db.commit()
    b_ticket_id = b_ticket.id

    monkeypatch.setattr(
        zendesk_client,
        "get_ticket",
        _fake_zendesk_ticket(requester_id=None),
    )

    try:
        async with make_client() as client:
            r = await client.post(
                f"/zendesk/tickets/{zid}/sync", headers=_h_alpha()
            )
        # Fail closed: never returns 200 and never reaches Org B's row.
        assert r.status_code == 409

        # Same external-id lookup through Org A tenant scope cannot cross.
        assert (
            await TicketRepository.get_by_external_id_for_tenant(
                db, str(zid), tenant_data["org_a"].id
            )
        ) is None

        unchanged = await TicketRepository.get_by_id_for_tenant(
            db, b_ticket_id, tenant_data["org_b"].id
        )
        assert unchanged is not None
        assert unchanged.subject == "Org B owned"
        assert unchanged.status == "new"
    finally:
        await db.rollback()
        await db.execute(delete(Ticket).where(Ticket.id == b_ticket_id))
        await db.commit()


@pytest.mark.asyncio
async def test_tenant_sync_creates_tenant_owned_ticket(
    monkeypatch, make_client, db, tenant_data
):
    from app.integrations.zendesk.client import zendesk_client
    from app.repositories.ticket_repository import TicketRepository

    zid = _zendesk_external_id()
    monkeypatch.setattr(
        zendesk_client,
        "get_ticket",
        _fake_zendesk_ticket(requester_id=None),
    )

    async with make_client() as client:
        r = await client.post(
            f"/zendesk/tickets/{zid}/sync", headers=_h_alpha()
        )

    created_id = None
    try:
        assert r.status_code == 200
        body = r.json()
        # Tenant sync never creates an organization_id=NULL ticket (E).
        assert body["organization_id"] == tenant_data["org_a"].id
        assert body["external_id"] == str(zid)
        created_id = body["id"]

        in_db = await TicketRepository.get_by_external_id_for_tenant(
            db, str(zid), tenant_data["org_a"].id
        )
        assert in_db is not None
        assert in_db.organization_id == tenant_data["org_a"].id
    finally:
        await db.rollback()
        if created_id is not None:
            await db.execute(delete(Ticket).where(Ticket.id == created_id))
            await db.commit()


@pytest.mark.asyncio
async def test_tenant_sync_cannot_reuse_foreign_customer(
    monkeypatch, make_client, db, tenant_data
):
    from app.integrations.zendesk.client import zendesk_client
    from app.repositories.customer_repository import CustomerRepository

    b_email = f"b-extra-{uuid.uuid4().hex[:8]}@example.com"
    b_extra = Customer(
        name="B Extra",
        email=b_email,
        organization_id=tenant_data["org_b"].id,
    )
    db.add(b_extra)
    await db.commit()
    b_extra_id = b_extra.id

    zid = _zendesk_external_id()
    monkeypatch.setattr(
        zendesk_client, "get_ticket", _fake_zendesk_ticket(requester_id=9_001)
    )
    monkeypatch.setattr(
        zendesk_client, "get_user", _fake_zendesk_user(b_email)
    )

    try:
        async with make_client() as client:
            r = await client.post(
                f"/zendesk/tickets/{zid}/sync", headers=_h_alpha()
            )
        # Org A cannot attach Org B's customer; sync fails closed rather than
        # reusing the foreign row.
        assert r.status_code == 409

        still_b = await CustomerRepository.get_by_id_for_tenant(
            db, b_extra_id, tenant_data["org_b"].id
        )
        assert still_b is not None
        assert still_b.email == b_email
    finally:
        await db.rollback()
        await db.execute(delete(Customer).where(Customer.id == b_extra_id))
        await db.commit()


# ===================================================================
# H2 remediation: automation rules are tenant-owned
# ===================================================================
@pytest.mark.asyncio
async def test_automation_rule_update_null_required_field_422(
    make_client, db, tenant_data
):
    from app.models.automation_rule import AutomationRule

    async with make_client() as client:
        r = await client.post(
            "/automation-rules",
            headers=_h_alpha(),
            json={
                "name": f"null-{uuid.uuid4().hex[:8]}",
                "event_type": "ticket.created",
                "enabled": True,
                "conditions": {},
                "actions": {},
            },
        )
    assert r.status_code == 201
    rule_id = r.json()["id"]

    try:
        async with make_client() as client:
            r = await client.patch(
                f"/automation-rules/{rule_id}",
                headers=_h_alpha(),
                json={"priority": None},
            )
        assert r.status_code == 422

        async with make_client() as client:
            r = await client.patch(
                f"/automation-rules/{rule_id}",
                headers=_h_alpha(),
                json={"enabled": None},
            )
        assert r.status_code == 422
    finally:
        await db.rollback()
        await db.execute(delete(AutomationRule).where(AutomationRule.id == rule_id))
        await db.commit()


@pytest.mark.asyncio
async def test_automation_rule_created_in_current_tenant(make_client, db, tenant_data):
    from app.models.automation_rule import AutomationRule
    from app.repositories.automation_rule_repository import AutomationRuleRepository

    async with make_client() as client:
        r = await client.post(
            "/automation-rules",
            headers=_h_alpha(),
            json={
                "name": f"alpha-rule-{uuid.uuid4().hex[:8]}",
                "event_type": "ticket.created",
                "enabled": True,
                "conditions": {"any_keywords": ["urgent"]},
                "actions": {"status": "solved"},
            },
        )
    assert r.status_code == 201
    body = r.json()
    assert body["organization_id"] == tenant_data["org_a"].id
    rule_id = body["id"]

    try:
        scoped = await AutomationRuleRepository.get_by_id_for_tenant(
            db, rule_id, tenant_data["org_a"].id
        )
        assert scoped is not None
        assert scoped.organization_id == tenant_data["org_a"].id
    finally:
        await db.rollback()
        await db.execute(delete(AutomationRule).where(AutomationRule.id == rule_id))
        await db.commit()


@pytest.mark.asyncio
async def test_automation_rule_tenant_isolation(make_client, db, tenant_data):
    from app.models.automation_rule import AutomationRule
    from app.repositories.automation_rule_repository import AutomationRuleRepository

    async with make_client() as client:
        r = await client.post(
            "/automation-rules",
            headers=_h_beta(),
            json={
                "name": f"beta-rule-{uuid.uuid4().hex[:8]}",
                "event_type": "ticket.created",
                "enabled": True,
                "conditions": {},
                "actions": {"status": "solved"},
            },
        )
    assert r.status_code == 201
    beta_rule_id = r.json()["id"]
    assert r.json()["organization_id"] == tenant_data["org_b"].id

    try:
        async with make_client() as client:
            r = await client.get("/automation-rules", headers=_h_alpha())
        assert r.status_code == 200
        assert all(x["id"] != beta_rule_id for x in r.json())

        async with make_client() as client:
            r = await client.get(
                f"/automation-rules/{beta_rule_id}", headers=_h_alpha()
            )
        assert r.status_code == 404

        async with make_client() as client:
            r = await client.patch(
                f"/automation-rules/{beta_rule_id}",
                headers=_h_alpha(),
                json={"enabled": False},
            )
        assert r.status_code == 404

        beta_rules = await AutomationRuleRepository.get_active_for_event_for_tenant(
            db, tenant_data["org_b"].id, "ticket.created"
        )
        assert any(x.id == beta_rule_id for x in beta_rules)

        alpha_rules = await AutomationRuleRepository.get_active_for_event_for_tenant(
            db, tenant_data["org_a"].id, "ticket.created"
        )
        assert all(x.id != beta_rule_id for x in alpha_rules)
    finally:
        await db.rollback()
        await db.execute(delete(AutomationRule).where(AutomationRule.id == beta_rule_id))
        await db.commit()


@pytest.mark.asyncio
async def test_automation_rule_cannot_mutate_foreign_ticket(
    make_client, db, tenant_data
):
    from app.models.automation_rule import AutomationRule
    from app.repositories.ticket_repository import TicketRepository
    from app.services.automation_service import AutomationService

    async with make_client() as client:
        r = await client.post(
            "/automation-rules",
            headers=_h_alpha(),
            json={
                "name": f"mut-{uuid.uuid4().hex[:8]}",
                "event_type": "ticket.created",
                "enabled": True,
                "conditions": {},
                "actions": {"status": "solved"},
            },
        )
    assert r.status_code == 201
    rule_id = r.json()["id"]

    event = TicketEvent(
        event_key=f"evt-{uuid.uuid4().hex}",
        event_type="ticket.created",
        source="test",
        ticket_id=tenant_data["ticket_b"].id,
        payload={},
    )
    db.add(event)
    await db.commit()
    event_id = event.id

    try:
        await AutomationService.process_ticket_event(db, event)

        b_ticket = await TicketRepository.get_by_id_for_tenant(
            db, tenant_data["ticket_b"].id, tenant_data["org_b"].id
        )
        assert b_ticket is not None
        assert b_ticket.status == "new"
    finally:
        await db.rollback()
        await db.execute(delete(TicketEvent).where(TicketEvent.id == event_id))
        await db.execute(delete(AutomationRule).where(AutomationRule.id == rule_id))
        await db.commit()


@pytest.mark.asyncio
async def test_legacy_null_org_rule_is_inert(db, tenant_data):
    from app.models.automation_rule import AutomationRule
    from app.repositories.ticket_repository import TicketRepository
    from app.services.automation_service import AutomationService

    legacy = AutomationRule(
        name=f"legacy-{uuid.uuid4().hex[:8]}",
        event_type="ticket.created",
        enabled=True,
        priority=1,
        conditions={},
        actions={"status": "solved"},
    )
    db.add(legacy)
    await db.commit()
    legacy_id = legacy.id
    assert legacy.organization_id is None

    event = TicketEvent(
        event_key=f"evt-{uuid.uuid4().hex}",
        event_type="ticket.created",
        source="test",
        ticket_id=tenant_data["ticket_a"].id,
        payload={},
    )
    db.add(event)
    await db.commit()
    event_id = event.id

    try:
        await AutomationService.process_ticket_event(db, event)

        a_ticket = await TicketRepository.get_by_id_for_tenant(
            db, tenant_data["ticket_a"].id, tenant_data["org_a"].id
        )
        assert a_ticket is not None
        assert a_ticket.status == "new"
    finally:
        await db.rollback()
        await db.execute(delete(TicketEvent).where(TicketEvent.id == event_id))
        await db.execute(delete(AutomationRule).where(AutomationRule.id == legacy_id))
        await db.commit()


@pytest.mark.asyncio
async def test_automation_rule_multi_membership_selector(make_client, db, two_orgs):
    from app.models.automation_rule import AutomationRule

    org_a, org_b = two_orgs
    db.add(OrganizationMembership(subject=USER_ALPHA, organization_id=org_b.id))
    await db.commit()

    rule_id = None
    try:
        headers = {**_h_alpha(), X_TENANT: str(org_a.id)}
        async with make_client() as client:
            r = await client.post(
                "/automation-rules",
                headers=headers,
                json={
                    "name": f"msel-{uuid.uuid4().hex[:8]}",
                    "event_type": "ticket.created",
                    "enabled": True,
                    "conditions": {},
                    "actions": {"status": "solved"},
                },
            )
        assert r.status_code == 201
        assert r.json()["organization_id"] == org_a.id
        rule_id = r.json()["id"]
    finally:
        await db.rollback()
        if rule_id is not None:
            await db.execute(delete(AutomationRule).where(AutomationRule.id == rule_id))
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.subject == USER_ALPHA,
                OrganizationMembership.organization_id == org_b.id,
            )
        )
        await db.commit()