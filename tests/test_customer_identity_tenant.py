"""Phase 1E.4 tenant-scoped customer identity integration tests.

Covers the CustomerIdentity DB/repository layer, Zendesk sync identity
creation, identity + context API tenant isolation, concurrent identity
resolution through the database unique constraint, and context tenancy.

Roles used: user-alpha (OWNER at Org A), user-beta (OWNER at Org B),
user-viewer (VIEWER at Org A).
"""

import asyncio
import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-identity-tenant-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-identity-tenant-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.customer import Customer
from app.models.customer_identity import CustomerIdentity
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-identity-tenant-issuer"
TEST_AUDIENCE = "test-identity-tenant-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"
USER_VIEWER = "user-viewer"


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


def _build_token(*, sub: str) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": now + 3600,
        "iat": now,
    }
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


def _h_alpha():
    return {"Authorization": f"Bearer {_build_token(sub=USER_ALPHA)}"}


def _h_beta():
    return {"Authorization": f"Bearer {_build_token(sub=USER_BETA)}"}


def _h_viewer():
    return {"Authorization": f"Bearer {_build_token(sub=USER_VIEWER)}"}


@pytest.fixture
def make_client():
    def _make():
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    return _make


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def tenant(db):
    """Org A (alpha OWNER + viewer VIEWER), Org B (beta OWNER)."""
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.subject.in_(
                [USER_ALPHA, USER_BETA, USER_VIEWER]
            )
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
            OrganizationMembership(
                subject=USER_VIEWER,
                organization_id=org_a_id,
                role=OrganizationRole.VIEWER,
            ),
        ]
    )
    await db.commit()

    unique_alpha = f"alpha-{uuid.uuid4().hex[:8]}@example.com"
    unique_beta = f"beta-{uuid.uuid4().hex[:8]}@example.com"

    cust_a = Customer(
        name="Alpha Customer",
        email=unique_alpha,
        organization_id=org_a_id,
    )
    cust_b = Customer(
        name="Beta Customer",
        email=unique_beta,
        organization_id=org_b_id,
    )
    db.add_all([cust_a, cust_b])
    await db.commit()
    cust_a_id = cust_a.id
    cust_b_id = cust_b.id

    try:
        yield {
            "org_a_id": org_a_id,
            "org_b_id": org_b_id,
            "cust_a_id": cust_a_id,
            "cust_b_id": cust_b_id,
            "email_alpha": unique_alpha,
            "email_beta": unique_beta,
        }
    finally:
        await db.rollback()
        await db.execute(
            delete(CustomerIdentity).where(
                CustomerIdentity.customer_id.in_([cust_a_id, cust_b_id])
            )
        )
        await db.execute(
            delete(Customer).where(Customer.id.in_([cust_a_id, cust_b_id]))
        )
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.subject.in_(
                    [USER_ALPHA, USER_BETA, USER_VIEWER]
                )
            )
        )
        await db.execute(
            delete(Organization).where(Organization.id.in_([org_a_id, org_b_id]))
        )
        await db.commit()


async def _identity_rows(db, *, organization_id, customer_id) -> list[CustomerIdentity]:
    result = await db.execute(
        select(CustomerIdentity).where(
            CustomerIdentity.organization_id == organization_id,
            CustomerIdentity.customer_id == customer_id,
        )
    )
    return list(result.scalars().all())


class TestFingerprintDigestSensitivity:
    def test_fingerprint_changes_with_context_digest(self):
        from app.services.agent_workflow_service import AgentWorkflowService

        base = AgentWorkflowService._compute_fingerprint(
            organization_id=1,
            ticket_id=2,
            ticket={"subject": "a", "description": "b", "status": "open", "priority": "normal"},
            agent_decision_version="1",
            model="gpt-test",
            corpus_revision={},
            customer_context_digest="digest-a",
        )
        changed = AgentWorkflowService._compute_fingerprint(
            organization_id=1,
            ticket_id=2,
            ticket={"subject": "a", "description": "b", "status": "open", "priority": "normal"},
            agent_decision_version="1",
            model="gpt-test",
            corpus_revision={},
            customer_context_digest="digest-b",
        )
        assert base != changed

    def test_fingerprint_stable_for_same_context_digest(self):
        from app.services.agent_workflow_service import AgentWorkflowService

        kwargs = {
            "organization_id": 1,
            "ticket_id": 2,
            "ticket": {"subject": "a", "description": "b", "status": "open", "priority": "normal"},
            "agent_decision_version": "1",
            "model": "gpt-test",
            "corpus_revision": {},
            "customer_context_digest": "digest-a",
        }
        first = AgentWorkflowService._compute_fingerprint(**kwargs)
        second = AgentWorkflowService._compute_fingerprint(**kwargs)
        assert first == second


def _fake_zendesk_ticket(subject="Zendesk Synced", requester_id=None):
    async def _get_ticket(db, ticket_id, *, organization_id=None):
        return {
            "ticket": {
                "id": ticket_id,
                "subject": subject,
                "description": "Synced from Zendesk",
                "requester_id": requester_id,
                "status": "open",
                "priority": "high",
            }
        }

    return _get_ticket


def _fake_zendesk_user(email, name="Zendesk Requester"):
    async def _get_user(db, user_id, *, organization_id=None):
        return {"user": {"id": user_id, "email": email, "name": name}}

    return _get_user


async def _fake_zendesk_comments(db, ticket_id, *, organization_id=None):
    return {"comments": []}


def _zendesk_external_id() -> int:
    return (int(uuid.uuid4().hex[:12], 16) % 900_000_000) + 100_000_000


# ===================================================================
# CustomerIdentity DB integration
# ===================================================================


@pytest.mark.asyncio
async def test_link_identity_creates_and_lists_row(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    identity = await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9001",
    )
    assert identity.customer_id == tenant["cust_a_id"]
    assert identity.normalized_identifier == "9001"

    rows = await _identity_rows(
        db, organization_id=tenant["org_a_id"], customer_id=tenant["cust_a_id"]
    )
    assert len(rows) == 1
    assert rows[0].provider == "zendesk"
    assert rows[0].identity_type == "user_id"


@pytest.mark.asyncio
async def test_same_provider_identity_isolated_per_tenant(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9001",
    )
    b_identity = await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_b_id"],
        customer_id=tenant["cust_b_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9001",
    )
    assert b_identity.customer_id == tenant["cust_b_id"]

    a_rows = await _identity_rows(
        db, organization_id=tenant["org_a_id"], customer_id=tenant["cust_a_id"]
    )
    b_rows = await _identity_rows(
        db, organization_id=tenant["org_b_id"], customer_id=tenant["cust_b_id"]
    )
    assert len(a_rows) == 1
    assert len(b_rows) == 1


@pytest.mark.asyncio
async def test_link_identity_conflict_other_customer_same_tenant(db, tenant):
    from app.services.customer_identity_service import (
        CustomerIdentityConflictError,
        CustomerIdentityService,
    )

    other = Customer(
        name="Other Alpha",
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=tenant["org_a_id"],
    )
    db.add(other)
    await db.commit()
    other_id = other.id

    try:
        await CustomerIdentityService.link_identity(
            db,
            organization_id=tenant["org_a_id"],
            customer_id=tenant["cust_a_id"],
            provider="zendesk",
            identity_type="user_id",
            identifier="9001",
        )
        with pytest.raises(CustomerIdentityConflictError):
            await CustomerIdentityService.link_identity(
                db,
                organization_id=tenant["org_a_id"],
                customer_id=other_id,
                provider="zendesk",
                identity_type="user_id",
                identifier="9001",
            )

        other_rows = await _identity_rows(
            db, organization_id=tenant["org_a_id"], customer_id=other_id
        )
        assert other_rows == []
    finally:
        await db.rollback()
        await db.execute(delete(Customer).where(Customer.id == other_id))
        await db.commit()


@pytest.mark.asyncio
async def test_duplicate_link_identity_is_idempotent(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    first = await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9001",
    )
    second = await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9001",
    )
    assert second.id == first.id

    rows = await _identity_rows(
        db, organization_id=tenant["org_a_id"], customer_id=tenant["cust_a_id"]
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_resolve_by_provider_identity_normalizes_leading_zeros(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="6119",
    )

    resolved = await CustomerIdentityService.resolve_by_provider_identity(
        db,
        organization_id=tenant["org_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="006119",
    )
    assert resolved is not None
    assert resolved.customer_id == tenant["cust_a_id"]


@pytest.mark.asyncio
async def test_resolve_or_create_creates_customer_and_identity(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    email = f"requester-{uuid.uuid4().hex[:8]}@example.com"
    customer, identity = (
        await CustomerIdentityService.resolve_or_create_customer_by_provider_identity(
            db,
            organization_id=tenant["org_a_id"],
            provider="zendesk",
            identity_type="user_id",
            identifier="9002",
            display_name="Requester Two",
            email=email,
        )
    )
    customer_id = customer.id

    try:
        assert customer.organization_id == tenant["org_a_id"]
        assert customer.email == email
        assert identity.customer_id == customer_id
        assert identity.normalized_identifier == "9002"

        again_customer, again_identity = (
            await CustomerIdentityService.resolve_or_create_customer_by_provider_identity(
                db,
                organization_id=tenant["org_a_id"],
                provider="zendesk",
                identity_type="user_id",
                identifier="9002",
                display_name="Requester Two",
                email=email,
            )
        )
        assert again_customer.id == customer_id
        assert again_identity.id == identity.id

        rows = await _identity_rows(
            db, organization_id=tenant["org_a_id"], customer_id=customer_id
        )
        assert len(rows) == 1
    finally:
        await db.rollback()
        await db.execute(
            delete(CustomerIdentity).where(CustomerIdentity.customer_id == customer_id)
        )
        await db.execute(delete(Customer).where(Customer.id == customer_id))
        await db.commit()


@pytest.mark.asyncio
async def test_resolve_or_create_reuses_customer_by_email(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    customer, identity = (
        await CustomerIdentityService.resolve_or_create_customer_by_provider_identity(
            db,
            organization_id=tenant["org_a_id"],
            provider="zendesk",
            identity_type="user_id",
            identifier="9003",
            display_name="Requester Three",
            email=tenant["email_alpha"],
        )
    )

    try:
        assert customer.id == tenant["cust_a_id"]
        assert identity.customer_id == tenant["cust_a_id"]

        rows = await _identity_rows(
            db,
            organization_id=tenant["org_a_id"],
            customer_id=tenant["cust_a_id"],
        )
        assert len(rows) == 1
        assert rows[0].normalized_identifier == "9003"
    finally:
        await db.rollback()
        await db.execute(
            delete(CustomerIdentity).where(CustomerIdentity.customer_id == tenant["cust_a_id"])
        )
        await db.commit()


@pytest.mark.asyncio
async def test_resolve_or_create_identity_authoritative_then_email_conflict(db, tenant):
    from app.services.customer_identity_service import (
        CustomerIdentityConflictError,
        CustomerIdentityService,
    )

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9004",
    )
    conflict_email = f"conflict-{uuid.uuid4().hex[:8]}@example.com"
    other = Customer(
        name="Other Alpha",
        email=conflict_email,
        organization_id=tenant["org_a_id"],
    )
    db.add(other)
    await db.commit()
    other_id = other.id

    try:
        # The existing provider identity is authoritative: resolve returns the
        # identity owner even though the email belongs to a different customer.
        customer, identity = (
            await CustomerIdentityService.resolve_or_create_customer_by_provider_identity(
                db,
                organization_id=tenant["org_a_id"],
                provider="zendesk",
                identity_type="user_id",
                identifier="9004",
                display_name="Requester Four",
                email=conflict_email,
            )
        )
        assert customer.id == tenant["cust_a_id"]
        assert identity.customer_id == tenant["cust_a_id"]

        # The safe email update must then fail closed instead of overwriting a
        # different customer's email.
        with pytest.raises(CustomerIdentityConflictError):
            await CustomerIdentityService.safe_update_customer_email(
                db,
                customer=customer,
                new_email=conflict_email,
                organization_id=tenant["org_a_id"],
            )
        await db.rollback()

        still_other = await db.get(Customer, other_id, with_for_update=False)
        assert still_other is not None
        assert still_other.email == conflict_email
    finally:
        await db.rollback()
        await db.execute(delete(Customer).where(Customer.id == other_id))
        await db.commit()


@pytest.mark.asyncio
async def test_safe_email_update_unused_email_updates(db, tenant):
    from app.models.customer import Customer as CustomerModel
    from app.services.customer_identity_service import CustomerIdentityService

    customer = await db.get(
        CustomerModel, tenant["cust_a_id"], with_for_update=False
    )
    assert customer is not None

    new_email = f"updated-{uuid.uuid4().hex[:8]}@example.com"
    updated = await CustomerIdentityService.safe_update_customer_email(
        db,
        customer=customer,
        new_email=new_email,
        organization_id=tenant["org_a_id"],
    )
    assert updated.email == new_email


@pytest.mark.asyncio
async def test_safe_email_update_conflicting_email_raises(db, tenant):
    from app.models.customer import Customer as CustomerModel
    from app.services.customer_identity_service import (
        CustomerIdentityConflictError,
        CustomerIdentityService,
    )

    conflict_email = f"conflict-{uuid.uuid4().hex[:8]}@example.com"
    other = Customer(
        name="Email Owner",
        email=conflict_email,
        organization_id=tenant["org_a_id"],
    )
    db.add(other)
    await db.commit()
    other_id = other.id

    customer = await db.get(CustomerModel, tenant["cust_a_id"], with_for_update=False)
    assert customer is not None

    try:
        with pytest.raises(CustomerIdentityConflictError):
            await CustomerIdentityService.safe_update_customer_email(
                db,
                customer=customer,
                new_email=conflict_email,
                organization_id=tenant["org_a_id"],
            )
        await db.rollback()
    finally:
        await db.rollback()
        await db.execute(delete(Customer).where(Customer.id == other_id))
        await db.commit()


@pytest.mark.asyncio
async def test_safe_email_update_none_uses_placeholder(db, tenant):
    from app.models.customer import Customer as CustomerModel
    from app.services.customer_identity_service import CustomerIdentityService

    customer = await db.get(CustomerModel, tenant["cust_a_id"], with_for_update=False)
    assert customer is not None

    updated = await CustomerIdentityService.safe_update_customer_email(
        db,
        customer=customer,
        new_email=None,
        organization_id=tenant["org_a_id"],
    )
    assert "@placeholder.local" in updated.email


@pytest.mark.asyncio
async def test_customer_identity_cascade_on_customer_delete(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    customer, _identity = (
        await CustomerIdentityService.resolve_or_create_customer_by_provider_identity(
            db,
            organization_id=tenant["org_a_id"],
            provider="zendesk",
            identity_type="user_id",
            identifier="9005",
            display_name="Cascade Target",
            email=f"cascade-{uuid.uuid4().hex[:8]}@example.com",
        )
    )
    customer_id = customer.id

    await db.execute(delete(Customer).where(Customer.id == customer_id))
    await db.commit()

    rows = await _identity_rows(
        db, organization_id=tenant["org_a_id"], customer_id=customer_id
    )
    assert rows == []


# ===================================================================
# Zendesk sync identity creation
# ===================================================================


@pytest.mark.asyncio
async def test_sync_creates_identity_and_links_customer(
    monkeypatch, make_client, db, tenant
):
    from app.integrations.zendesk.client import zendesk_client

    requester_id = 9_101
    requester_email = f"r-{uuid.uuid4().hex[:8]}@example.com"
    zid = _zendesk_external_id()
    monkeypatch.setattr(
        zendesk_client,
        "get_ticket",
        _fake_zendesk_ticket(requester_id=requester_id),
    )
    monkeypatch.setattr(
        zendesk_client, "get_user", _fake_zendesk_user(requester_email)
    )
    monkeypatch.setattr(
        zendesk_client, "get_ticket_comments", _fake_zendesk_comments
    )

    ticket_id = None
    customer_id = None
    try:
        async with make_client() as client:
            r = await client.post(f"/zendesk/tickets/{zid}/sync", headers=_h_alpha())
        assert r.status_code == 200
        body = r.json()
        ticket_id = body["id"]
        customer_id = body["customer_id"]
        assert customer_id is not None

        rows = await _identity_rows(
            db, organization_id=tenant["org_a_id"], customer_id=customer_id
        )
        assert len(rows) == 1
        assert rows[0].provider == "zendesk"
        assert rows[0].identity_type == "user_id"
        assert rows[0].normalized_identifier == str(requester_id)

        customer = await db.get(Customer, customer_id, with_for_update=False)
        assert customer is not None
        assert customer.email == requester_email
    finally:
        await db.rollback()
        if ticket_id is not None:
            await db.execute(delete(Ticket).where(Ticket.id == ticket_id))
        if customer_id is not None:
            await db.execute(
                delete(CustomerIdentity).where(
                    CustomerIdentity.customer_id == customer_id
                )
            )
            await db.execute(delete(Customer).where(Customer.id == customer_id))
        await db.commit()


@pytest.mark.asyncio
async def test_sync_same_requester_resolves_same_customer(
    monkeypatch, make_client, db, tenant
):
    from app.integrations.zendesk.client import zendesk_client
    from app.repositories.customer_repository import CustomerRepository

    requester_id = 9_102
    requester_email = f"r-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(
        zendesk_client,
        "get_ticket",
        _fake_zendesk_ticket(requester_id=requester_id),
    )
    monkeypatch.setattr(
        zendesk_client, "get_user", _fake_zendesk_user(requester_email)
    )
    monkeypatch.setattr(
        zendesk_client, "get_ticket_comments", _fake_zendesk_comments
    )

    zid1 = _zendesk_external_id()
    zid2 = _zendesk_external_id()

    ticket_ids = []
    customer_id = None
    try:
        for zid in (zid1, zid2):
            async with make_client() as client:
                r = await client.post(
                    f"/zendesk/tickets/{zid}/sync", headers=_h_alpha()
                )
            assert r.status_code == 200
            body = r.json()
            ticket_ids.append(body["id"])
            if customer_id is None:
                customer_id = body["customer_id"]
            else:
                assert body["customer_id"] == customer_id

        rows = await _identity_rows(
            db, organization_id=tenant["org_a_id"], customer_id=customer_id
        )
        assert len(rows) == 1

        customers = await CustomerRepository.list_for_tenant(
            db, tenant["org_a_id"]
        )
        found = [c for c in customers if c.id == customer_id]
        assert len(found) == 1
    finally:
        await db.rollback()
        if ticket_ids:
            await db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
        if customer_id is not None:
            await db.execute(
                delete(CustomerIdentity).where(
                    CustomerIdentity.customer_id == customer_id
                )
            )
            await db.execute(delete(Customer).where(Customer.id == customer_id))
        await db.commit()


@pytest.mark.asyncio
async def test_sync_same_requester_new_email_updates_customer_email(
    monkeypatch, make_client, db, tenant
):
    from app.integrations.zendesk.client import zendesk_client

    requester_id = 9_103
    zid = _zendesk_external_id()
    monkeypatch.setattr(
        zendesk_client,
        "get_ticket",
        _fake_zendesk_ticket(requester_id=requester_id),
    )
    monkeypatch.setattr(
        zendesk_client, "get_user", _fake_zendesk_user("first@example.com")
    )
    monkeypatch.setattr(
        zendesk_client, "get_ticket_comments", _fake_zendesk_comments
    )

    ticket_ids = []
    customer_id = None
    try:
        async with make_client() as client:
            r = await client.post(f"/zendesk/tickets/{zid}/sync", headers=_h_alpha())
        assert r.status_code == 200
        body = r.json()
        ticket_ids.append(body["id"])
        customer_id = body["customer_id"]

        monkeypatch.setattr(
            zendesk_client,
            "get_user",
            _fake_zendesk_user("second@example.com"),
        )
        zid2 = _zendesk_external_id()
        async with make_client() as client:
            r2 = await client.post(
                f"/zendesk/tickets/{zid2}/sync", headers=_h_alpha()
            )
        assert r2.status_code == 200
        body2 = r2.json()
        ticket_ids.append(body2["id"])
        assert body2["customer_id"] == customer_id

        customer = await db.get(Customer, customer_id, with_for_update=False)
        assert customer is not None
        assert customer.email == "second@example.com"

        rows = await _identity_rows(
            db, organization_id=tenant["org_a_id"], customer_id=customer_id
        )
        assert len(rows) == 1
    finally:
        await db.rollback()
        if ticket_ids:
            await db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
        if customer_id is not None:
            await db.execute(
                delete(CustomerIdentity).where(
                    CustomerIdentity.customer_id == customer_id
                )
            )
            await db.execute(delete(Customer).where(Customer.id == customer_id))
        await db.commit()


@pytest.mark.asyncio
async def test_sync_requester_identity_conflict_fails_closed(
    monkeypatch, make_client, db, tenant
):
    from app.integrations.zendesk.client import zendesk_client
    from app.services.customer_identity_service import CustomerIdentityService

    requester_id = 9_104
    conflict_email = f"conflict-{uuid.uuid4().hex[:8]}@example.com"

    # Pre-map the Zendesk requester id to Org A's existing customer.
    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier=str(requester_id),
    )
    # The requester email already belongs to a DIFFERENT customer in Org A.
    other = Customer(
        name="Requester Owner",
        email=conflict_email,
        organization_id=tenant["org_a_id"],
    )
    db.add(other)
    await db.commit()
    other_id = other.id

    monkeypatch.setattr(
        zendesk_client,
        "get_ticket",
        _fake_zendesk_ticket(requester_id=requester_id),
    )
    monkeypatch.setattr(
        zendesk_client, "get_user", _fake_zendesk_user(conflict_email)
    )

    zid = _zendesk_external_id()
    try:
        async with make_client() as client:
            r = await client.post(f"/zendesk/tickets/{zid}/sync", headers=_h_alpha())
        assert r.status_code == 409
        assert "Zendesk ticket is already linked" in r.json()["detail"]

        still_other = await db.get(Customer, other_id, with_for_update=False)
        assert still_other is not None
        assert still_other.email == conflict_email
        assert still_other.id != tenant["cust_a_id"]
    finally:
        await db.rollback()
        await db.execute(
            delete(CustomerIdentity).where(
                CustomerIdentity.customer_id == tenant["cust_a_id"]
            )
        )
        if other_id is not None:
            await db.execute(delete(Customer).where(Customer.id == other_id))
        await db.commit()


@pytest.mark.asyncio
async def test_sync_same_user_id_two_tenants_independent(
    monkeypatch, make_client, db, tenant
):
    from app.integrations.zendesk.client import zendesk_client

    requester_id = 9_105
    monkeypatch.setattr(
        zendesk_client,
        "get_ticket",
        _fake_zendesk_ticket(requester_id=requester_id),
    )
    monkeypatch.setattr(
        zendesk_client, "get_ticket_comments", _fake_zendesk_comments
    )

    a_email = f"a-{uuid.uuid4().hex[:8]}@example.com"
    b_email = f"b-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(zendesk_client, "get_user", _fake_zendesk_user(a_email))

    zid_a = _zendesk_external_id()
    zid_b = _zendesk_external_id()

    ticket_a_id = None
    ticket_b_id = None
    customer_a_id = None
    customer_b_id = None
    try:
        async with make_client() as client:
            r = await client.post(f"/zendesk/tickets/{zid_a}/sync", headers=_h_alpha())
        assert r.status_code == 200
        body = r.json()
        ticket_a_id = body["id"]
        customer_a_id = body["customer_id"]

        monkeypatch.setattr(zendesk_client, "get_user", _fake_zendesk_user(b_email))
        async with make_client() as client:
            r = await client.post(
                f"/zendesk/tickets/{zid_b}/sync", headers=_h_beta()
            )
        assert r.status_code == 200
        body = r.json()
        ticket_b_id = body["id"]
        customer_b_id = body["customer_id"]

        assert customer_a_id != customer_b_id

        a_rows = await _identity_rows(
            db, organization_id=tenant["org_a_id"], customer_id=customer_a_id
        )
        b_rows = await _identity_rows(
            db, organization_id=tenant["org_b_id"], customer_id=customer_b_id
        )
        assert len(a_rows) == 1
        assert len(b_rows) == 1
        assert a_rows[0].normalized_identifier == str(requester_id)
        assert b_rows[0].normalized_identifier == str(requester_id)
        assert a_rows[0].id != b_rows[0].id
    finally:
        await db.rollback()
        if ticket_a_id is not None:
            await db.execute(delete(Ticket).where(Ticket.id == ticket_a_id))
        if ticket_b_id is not None:
            await db.execute(delete(Ticket).where(Ticket.id == ticket_b_id))
        if customer_a_id is not None:
            await db.execute(
                delete(CustomerIdentity).where(
                    CustomerIdentity.customer_id == customer_a_id
                )
            )
            await db.execute(delete(Customer).where(Customer.id == customer_a_id))
        if customer_b_id is not None:
            await db.execute(
                delete(CustomerIdentity).where(
                    CustomerIdentity.customer_id == customer_b_id
                )
            )
            await db.execute(delete(Customer).where(Customer.id == customer_b_id))
        await db.commit()


# ===================================================================
# Concurrent identity resolution
# ===================================================================


@pytest.mark.asyncio
async def test_concurrent_resolve_or_create_single_customer_identity(db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    requester_id = 9_106
    email = f"concurrent-{uuid.uuid4().hex[:8]}@example.com"

    datasets = []

    async def _resolve():
        async with AsyncSessionLocal() as session:
            customer, identity = (
                await CustomerIdentityService.resolve_or_create_customer_by_provider_identity(
                    session,
                    organization_id=tenant["org_a_id"],
                    provider="zendesk",
                    identity_type="user_id",
                    identifier=str(requester_id),
                    display_name="Concurrent Requester",
                    email=email,
                )
            )
            datasets.append((customer.id, identity.id))

    await asyncio.gather(_resolve(), _resolve())

    assert len(datasets) == 2
    assert datasets[0][0] == datasets[1][0]
    assert datasets[0][1] == datasets[1][1]
    customer_id = datasets[0][0]

    rows = await _identity_rows(
        db, organization_id=tenant["org_a_id"], customer_id=customer_id
    )
    assert len(rows) == 1

    customer = await db.get(Customer, customer_id, with_for_update=False)
    assert customer is not None

    await db.rollback()
    await db.execute(
        delete(CustomerIdentity).where(CustomerIdentity.customer_id == customer_id)
    )
    await db.execute(delete(Customer).where(Customer.id == customer_id))
    await db.commit()


# ===================================================================
# Identity + context API tenant isolation
# ===================================================================


@pytest.mark.asyncio
async def test_get_identities_returns_list_for_owner(make_client, db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9007",
    )

    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant['cust_a_id']}/identities", headers=_h_alpha()
        )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["provider"] == "zendesk"
    assert item["identity_type"] == "user_id"
    assert item["identifier"] == "9007"


@pytest.mark.asyncio
async def test_get_identities_org_injection_404(make_client, db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_b_id"],
        customer_id=tenant["cust_b_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9008",
    )

    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant['cust_b_id']}/identities", headers=_h_alpha()
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_identities_viewer_can_read(make_client, db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9009",
    )

    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant['cust_a_id']}/identities", headers=_h_viewer()
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_identities_requires_auth(make_client):
    async with make_client() as client:
        r = await client.get("/customers/1/identities")
    assert r.status_code in (401, 403)


# Context API uses the same tenant fixture; cross-tenant context access must
# 404 like any other customer resource.
@pytest.mark.asyncio
async def test_get_context_cross_tenant_404(make_client, db, tenant):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant['cust_b_id']}/context", headers=_h_alpha()
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_context_owner_reads_bounded_context(make_client, db, tenant):
    from app.services.customer_identity_service import CustomerIdentityService

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9011",
    )

    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant['cust_a_id']}/context", headers=_h_alpha()
        )
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == tenant["cust_a_id"]
    assert "zendesk" in body["known_channels"]
    assert len(body["recent_tickets"]) == 0
    assert body["summary"]["total_tickets"] == 0


# ===================================================================
# Context tenancy (service level)
# ===================================================================


@pytest.mark.asyncio
async def test_context_privacy_excludes_hidden_payloads(db, tenant):
    """Context must never leak TicketEvent payloads or Agent reasoning.

    Only ticket subject/status/priority/category/source and agent activity
    type/source/title participate in the context; raw event payloads and
    analysis reasoning are excluded.
    """
    from app.models.agent_run import AgentRun
    from app.models.ticket_event import TicketEvent
    from app.services.customer_context_service import CustomerContextService

    customer = await db.get(Customer, tenant["cust_a_id"], with_for_update=False)
    assert customer is not None

    secret_note = "TOP_SECRET_PAYLOAD_EVENT"
    secret_reason = "TOP_SECRET_AGENT_REASON"
    secret_source = "TOP_SECRET_AGENT_SOURCE"
    secret_plan = "TOP_SECRET_AGENT_PLAN"

    ticket = Ticket(
        subject="Privacy Ticket",
        description="belongs to org a",
        status="open",
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
    )
    db.add(ticket)
    await db.flush()
    ticket_id = ticket.id

    event = TicketEvent(
        event_key=f"priv-{uuid.uuid4().hex}",
        event_type="ticket_created",
        source="zendesk",
        ticket_id=ticket_id,
        payload={"internal_note": secret_note},
    )
    db.add(event)

    run = AgentRun(
        run_id=f"priv-{uuid.uuid4().hex[:16]}",
        ticket_id=ticket_id,
        organization_id=tenant["org_a_id"],
        action="respond",
        reason=secret_reason,
        response_draft=secret_source,
        status="pending_approval",
        sources=[{"secret": secret_plan}],
    )
    db.add(run)
    await db.commit()

    try:
        context = await CustomerContextService.build_for_customer(
            db,
            organization_id=tenant["org_a_id"],
            customer=customer,
        )
        serialized = context.model_dump_json()

        assert secret_note not in serialized
        assert secret_reason not in serialized
        assert secret_source not in serialized
        assert secret_plan not in serialized

        assert len(serialized.encode("utf-8")) <= 8192
    finally:
        await db.rollback()
        await db.execute(
            delete(AgentRun).where(
                AgentRun.organization_id == tenant["org_a_id"],
                AgentRun.ticket_id == ticket_id,
            )
        )
        await db.execute(
            delete(TicketEvent).where(TicketEvent.ticket_id == ticket_id)
        )
        await db.execute(delete(Ticket).where(Ticket.id == ticket_id))
        await db.commit()


@pytest.mark.asyncio
async def test_context_service_cross_tenant_customer_raises(db, tenant):
    from app.services.customer_context_service import CustomerContextService

    with pytest.raises(ValueError):
        await CustomerContextService.build_for_ticket_customer(
            db,
            organization_id=tenant["org_a_id"],
            customer_id=tenant["cust_b_id"],
        )


@pytest.mark.asyncio
async def test_context_known_channels_from_identities(db, tenant):
    from app.services.customer_context_service import CustomerContextService
    from app.services.customer_identity_service import CustomerIdentityService

    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="zendesk",
        identity_type="user_id",
        identifier="9012",
    )
    await CustomerIdentityService.link_identity(
        db,
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
        provider="whatsapp",
        identity_type="phone",
        identifier="+15550001111",
    )

    customer = await db.get(Customer, tenant["cust_a_id"], with_for_update=False)
    assert customer is not None
    context = await CustomerContextService.build_for_customer(
        db,
        organization_id=tenant["org_a_id"],
        customer=customer,
    )
    assert context.known_channels == ["whatsapp", "zendesk"]
    assert context.customer_id == tenant["cust_a_id"]


@pytest.mark.asyncio
async def test_context_excludes_other_tenant_tickets(db, tenant):
    from app.services.customer_context_service import CustomerContextService

    customer = await db.get(Customer, tenant["cust_a_id"], with_for_update=False)
    assert customer is not None

    ticket_a = Ticket(
        subject="Alpha Visible",
        description="belongs to org a",
        status="open",
        organization_id=tenant["org_a_id"],
        customer_id=tenant["cust_a_id"],
    )
    ticket_b = Ticket(
        subject="Beta Hidden",
        description="belongs to org b",
        status="open",
        organization_id=tenant["org_b_id"],
        customer_id=tenant["cust_b_id"],
    )
    db.add_all([ticket_a, ticket_b])
    await db.commit()
    ticket_a_id = ticket_a.id
    ticket_b_id = ticket_b.id

    try:
        context = await CustomerContextService.build_for_customer(
            db,
            organization_id=tenant["org_a_id"],
            customer=customer,
        )
        subjects = [t.subject for t in context.recent_tickets]
        assert "Alpha Visible" in subjects
        assert "Beta Hidden" not in subjects
        assert context.summary.total_tickets == 1
    finally:
        await db.rollback()
        await db.execute(delete(Ticket).where(Ticket.id.in_([ticket_a_id, ticket_b_id])))
        await db.commit()