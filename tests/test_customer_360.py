"""End-to-end Customer 360 slice tests (Phase 1E.3).

Covers the 30 scenario groups from the spec: searchable customer list with
bounded pagination (1-8), cross-tenant detail isolation (9-10), linked tickets
(11-15), service summary (16-17), capability-composed unified timeline
(18-25), profile writes (26-29), and org-switch isolation (30).

Roles used: user-alpha (OWNER at Org A), user-beta (OWNER at Org B),
user-viewer (VIEWER at Org A). Every role in CXOps carries customer.read and
ticket.read, so the read gates are exercised through the role matrix; the
agent.run composition is exercised by comparing the VIEWER timeline (no agent
entries) with the OWNER timeline (agent entries present).
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-tenant-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-tenant-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.agent_run import AgentRun
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
USER_VIEWER = "user-viewer"

X_TENANT = "X-CXOps-Organization-ID"

EVENT_ALLOWED_KEYS = {
    "id",
    "type",
    "source",
    "occurred_at",
    "title",
    "summary",
    "ticket_id",
    "agent_run_id",
    "metadata",
}


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
async def tenant360(db):
    """Org A (alpha OWNER + viewer VIEWER), Org B (beta OWNER) with data."""
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

    unique_alpha = f"alpha{uuid.uuid4().hex[:8]}@example.com"
    unique_alpha2 = f"alpha2{uuid.uuid4().hex[:8]}@example.com"
    unique_beta = f"beta{uuid.uuid4().hex[:8]}@example.com"

    cust_a = Customer(
        name="Ada Alpha Customer",
        email=unique_alpha,
        phone="+10000000001",
        external_id="ext-10001",
        organization_id=org_a_id,
    )
    cust_a2 = Customer(
        name="Bob Alpha Two",
        email=unique_alpha2,
        organization_id=org_a_id,
    )
    cust_b = Customer(
        name="Beta Customer",
        email=unique_beta,
        organization_id=org_b_id,
    )
    db.add_all([cust_a, cust_a2, cust_b])
    await db.flush()

    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    t1 = datetime.now(timezone.utc)

    ticket_a1 = Ticket(
        subject="Open Billing Question",
        description="Billing question from customer A.",
        status="open",
        priority="high",
        category="billing",
        organization_id=org_a_id,
        customer_id=cust_a.id,
        created_at=t0,
        updated_at=t1,
    )
    ticket_a2 = Ticket(
        subject="Resolved Refund",
        description="Refund already processed.",
        status="solved",
        priority="normal",
        category="billing",
        organization_id=org_a_id,
        customer_id=cust_a.id,
        created_at=t1,
        updated_at=t1,
    )
    ticket_b = Ticket(
        subject="Beta Ticket",
        description="Beta case.",
        status="new",
        organization_id=org_b_id,
        customer_id=cust_b.id,
        created_at=t1,
        updated_at=t1,
    )
    db.add_all([ticket_a1, ticket_a2, ticket_b])
    await db.flush()

    event_a1 = TicketEvent(
        event_key=f"event-a1-{uuid.uuid4().hex}",
        event_type="ticket_updated",
        source="zendesk",
        ticket_id=ticket_a1.id,
        payload={"internal_note": "must never be exposed"},
        created_at=t1,
    )
    db.add(event_a1)

    run_a1 = AgentRun(
        run_id=f"run-{uuid.uuid4().hex[:16]}",
        ticket_id=ticket_a1.id,
        organization_id=org_a_id,
        action="respond",
        reason="Sensitive reasoning that must never surface.",
        response_draft="Sensitive draft that must never surface.",
        status="pending_approval",
        sources=[{"secret": "must never be exposed"}],
        workflow_path=["load_ticket", "draft_reply"],
        tool_plan=[{"tool": "zendesk.reply", "secret": "must never be exposed"}],
        reviewer_note="Sensitive review note.",
        created_at=t1,
    )
    db.add(run_a1)
    await db.commit()

    for obj in (cust_a, cust_a2, cust_b, ticket_a1, ticket_a2, ticket_b):
        await db.refresh(obj)

    cust_a_id = cust_a.id
    cust_a2_id = cust_a2.id
    cust_b_id = cust_b.id
    ticket_a1_id = ticket_a1.id
    ticket_a2_id = ticket_a2.id
    ticket_b_id = ticket_b.id
    event_a1_key = event_a1.event_key
    run_a1_id = run_a1.id
    run_a1_key = run_a1.run_id

    try:
        yield {
            "org_a_id": org_a_id,
            "org_b_id": org_b_id,
            "cust_a_id": cust_a_id,
            "cust_a2_id": cust_a2_id,
            "cust_b_id": cust_b_id,
            "ticket_a1_id": ticket_a1_id,
            "ticket_a2_id": ticket_a2_id,
            "ticket_b_id": ticket_b_id,
            "event_a1_key": event_a1_key,
            "run_a1_id": run_a1_id,
            "run_a1_key": run_a1_key,
            "email_alpha": unique_alpha,
            "email_alpha2": unique_alpha2,
            "email_beta": unique_beta,
        }
    finally:
        await db.rollback()
        await db.execute(
            delete(AgentRun).where(AgentRun.id == run_a1_id)
        )
        await db.execute(
            delete(TicketEvent).where(TicketEvent.event_key == event_a1_key)
        )
        await db.execute(
            delete(Ticket).where(Ticket.id.in_([ticket_a1_id, ticket_a2_id, ticket_b_id]))
        )
        await db.execute(
            delete(Customer).where(Customer.id.in_([cust_a_id, cust_a2_id, cust_b_id]))
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


def _envelope(body: dict) -> tuple[list, int]:
    assert set(body.keys()) == {"items", "total", "offset", "limit"}
    assert isinstance(body["total"], int)
    return body["items"], body["total"]


# ===================================================================
# 1-8. Searchable customer list with bounded pagination
# ===================================================================
@pytest.mark.asyncio
async def test_1_list_is_paginated_envelope_scoped_to_org(make_client, tenant360):
    async with make_client() as client:
        r = await client.get("/customers", headers=_h_alpha())
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert total == 2
    assert len(items) == 2
    emails = {item["email"] for item in items}
    assert emails == {
        tenant360["email_alpha"],
        tenant360["email_alpha2"],
    }
    assert all(item["organization_id"] == tenant360["org_a_id"] for item in items)


@pytest.mark.asyncio
async def test_2_search_by_name_partial_case_insensitive(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            "/customers", headers=_h_alpha(), params={"search": "ada"}
        )
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert total == 1
    assert items[0]["email"] == tenant360["email_alpha"]


@pytest.mark.asyncio
async def test_3_search_by_email_partial(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            "/customers",
            headers=_h_alpha(),
            params={"search": "alpha2"},
        )
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert total == 1
    assert items[0]["email"] == tenant360["email_alpha2"]


@pytest.mark.asyncio
async def test_4_search_by_external_id_is_exact(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            "/customers", headers=_h_alpha(), params={"search": "ext-10001"}
        )
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert total == 1
    assert items[0]["external_id"] == "ext-10001"

    async with make_client() as client:
        r2 = await client.get(
            "/customers", headers=_h_alpha(), params={"search": "10001"}
        )
    assert r2.status_code == 200
    _, total2 = _envelope(r2.json())
    assert total2 == 0


@pytest.mark.asyncio
async def test_5_limit_and_offset_pagination(make_client, tenant360):
    async with make_client() as client:
        r1 = await client.get(
            "/customers", headers=_h_alpha(), params={"limit": 1}
        )
        assert r1.status_code == 200
        items1, total1 = _envelope(r1.json())
        assert len(items1) == 1
        assert total1 == 2

        r2 = await client.get(
            "/customers", headers=_h_alpha(), params={"limit": 1, "offset": 1}
        )
        assert r2.status_code == 200
        items2, total2 = _envelope(r2.json())
        assert len(items2) == 1
        assert total2 == 2
        assert items1[0]["id"] != items2[0]["id"]


@pytest.mark.asyncio
async def test_6_offset_beyond_results_returns_empty(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            "/customers",
            headers=_h_alpha(),
            params={"offset": 50, "limit": 10},
        )
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert items == []
    assert total == 2


@pytest.mark.asyncio
async def test_7_limit_and_offset_bounds_validate(make_client, tenant360):
    async with make_client() as client:
        r1 = await client.get(
            "/customers", headers=_h_alpha(), params={"limit": 101}
        )
        assert r1.status_code == 422
        r2 = await client.get(
            "/customers", headers=_h_alpha(), params={"offset": -1}
        )
        assert r2.status_code == 422


@pytest.mark.asyncio
async def test_8_search_wildcards_treated_literally(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            "/customers", headers=_h_alpha(), params={"search": "%"}
        )
        assert r.status_code == 200
        _, total = _envelope(r.json())
        assert total == 0

        r2 = await client.get(
            "/customers", headers=_h_alpha(), params={"search": "_"}
        )
        assert r2.status_code == 200
        _, total2 = _envelope(r2.json())
        assert total2 == 0


# ===================================================================
# 9-10. Customer detail isolation
# ===================================================================
@pytest.mark.asyncio
async def test_9_detail_for_own_org(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}", headers=_h_alpha()
        )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == tenant360["email_alpha"]
    assert body["organization_id"] == tenant360["org_a_id"]
    assert body["external_id"] == "ext-10001"


@pytest.mark.asyncio
async def test_10_foreign_org_subresources_are_404(make_client, tenant360):
    # Detail, summary, tickets and timeline for a foreign customer are all
    # non-enumerating 404s.
    async with make_client() as client:
        r1 = await client.get(
            f"/customers/{tenant360['cust_b_id']}", headers=_h_alpha()
        )
        r2 = await client.get(
            f"/customers/{tenant360['cust_b_id']}/summary",
            headers=_h_alpha(),
        )
        r3 = await client.get(
            f"/customers/{tenant360['cust_b_id']}/tickets",
            headers=_h_alpha(),
        )
        r4 = await client.get(
            f"/customers/{tenant360['cust_b_id']}/timeline",
            headers=_h_alpha(),
        )
    assert r1.status_code == 404
    assert r2.status_code == 404
    assert r3.status_code == 404
    assert r4.status_code == 404


# ===================================================================
# 11-15. Linked tickets
# ===================================================================
@pytest.mark.asyncio
async def test_11_customer_tickets_envelope_scoped(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/tickets", headers=_h_alpha()
        )
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert total == 2
    assert len(items) == 2
    subjects = {item["subject"] for item in items}
    assert subjects == {"Open Billing Question", "Resolved Refund"}
    assert all(item["id"] in (tenant360["ticket_a1_id"], tenant360["ticket_a2_id"]) for item in items)
    # Compact payload: description/requester email are absent.
    assert "description" not in items[0]
    assert set(items[0].keys()) == {
        "id",
        "external_id",
        "subject",
        "status",
        "priority",
        "source",
        "category",
        "assigned_team",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_12_customer_tickets_required_capabilities_present(make_client, tenant360):
    # Both customer.read and ticket.read are enforced by the route deps; every
    # real role carries both, so an authorized OWNER sees the list.
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/tickets", headers=_h_alpha()
        )
    assert r.status_code == 200
    assert r.json()["total"] == 2


@pytest.mark.asyncio
async def test_13_customer_tickets_each_org_scoped(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/tickets", headers=_h_alpha()
        )
        r_foreign = await client.get(
            f"/customers/{tenant360['cust_b_id']}/tickets", headers=_h_alpha()
        )
        # Beta (other tenant) sees only their own customer's ticket.
        r_beta = await client.get(
            f"/customers/{tenant360['cust_b_id']}/tickets", headers=_h_beta()
        )
    assert r.status_code == 200
    assert r_foreign.status_code == 404
    assert r_beta.status_code == 200
    assert r_beta.json()["total"] == 1


@pytest.mark.asyncio
async def test_14_customer_tickets_pagination(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/tickets",
            headers=_h_alpha(),
            params={"limit": 1, "offset": 1},
        )
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert len(items) == 1
    assert total == 2
    assert items[0]["subject"] == "Open Billing Question"


@pytest.mark.asyncio
async def test_15_customer_without_tickets_returns_empty(make_client, tenant360):
    # cust_a2 has no tickets.
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a2_id']}/tickets", headers=_h_alpha()
        )
    assert r.status_code == 200
    items, total = _envelope(r.json())
    assert items == []
    assert total == 0


# ===================================================================
# 16-17. Service summary
# ===================================================================
@pytest.mark.asyncio
async def test_16_summary_metrics(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/summary", headers=_h_alpha()
        )
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == tenant360["cust_a_id"]
    assert body["total_tickets"] == 2
    assert body["open_tickets"] == 1
    assert body["closed_or_resolved_tickets"] == 1
    assert body["most_recent_ticket"]["subject"] == "Resolved Refund"
    assert body["common_category"] == "billing"
    assert body["latest_ticket_at"] is not None
    assert body["latest_interaction_at"] is not None


@pytest.mark.asyncio
async def test_17_summary_empty_and_foreign_404(make_client, tenant360):
    async with make_client() as client:
        r_empty = await client.get(
            f"/customers/{tenant360['cust_a2_id']}/summary", headers=_h_alpha()
        )
        r_foreign = await client.get(
            f"/customers/{tenant360['cust_b_id']}/summary", headers=_h_alpha()
        )
    assert r_empty.status_code == 200
    body = r_empty.json()
    assert body["total_tickets"] == 0
    assert body["open_tickets"] == 0
    assert body["closed_or_resolved_tickets"] == 0
    assert body["most_recent_ticket"] is None
    assert body["common_category"] is None
    assert r_foreign.status_code == 404


# ===================================================================
# 18-25. Capability-composed unified timeline
# ===================================================================
@pytest.mark.asyncio
async def test_18_timeline_envelope(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline", headers=_h_alpha()
        )
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "items",
        "total",
        "partial",
        "unavailable_sources",
    }
    assert body["partial"] is False
    assert body["unavailable_sources"] == []
    # 2 tickets + 1 ticket event + 1 agent run (OWNER has all capabilities)
    assert body["total"] == 4


@pytest.mark.asyncio
async def test_19_ticket_source_entries(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline", headers=_h_alpha()
        )
    body = r.json()
    ticket_entries = [
        event for event in body["items"] if event["source"] == "ticket"
    ]
    assert len(ticket_entries) == 2
    subjects = {event["title"] for event in ticket_entries}
    assert subjects == {"Open Billing Question", "Resolved Refund"}
    assert all(event["type"] == "ticket.created" for event in ticket_entries)


@pytest.mark.asyncio
async def test_20_ticket_event_entries_never_expose_payload(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline", headers=_h_alpha()
        )
    body = r.json()
    event_entries = [
        event for event in body["items"] if event["source"] == "ticket_event"
    ]
    assert len(event_entries) == 1
    assert event_entries[0]["type"] == "ticket_updated"
    assert event_entries[0]["metadata"] == {}
    assert "payload" not in event_entries[0]
    assert "internal_note" not in json_dumps(event_entries[0])


@pytest.mark.asyncio
async def test_21_timeline_viewer_excludes_agent_entries(make_client, tenant360):
    # VIEWER has customer.read + ticket.read but NOT agent.run: no agent source.
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline", headers=_h_viewer()
        )
    assert r.status_code == 200
    body = r.json()
    sources = {event["source"] for event in body["items"]}
    assert "agent" not in sources
    assert sources <= {"ticket", "ticket_event"}
    # Total excludes the hidden agent source.
    assert body["total"] == 3
    assert all(event["agent_run_id"] is None for event in body["items"])


@pytest.mark.asyncio
async def test_22_agent_entries_expose_only_allowlisted_fields(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline", headers=_h_alpha()
        )
    body = r.json()
    agent_entries = [
        event for event in body["items"] if event["source"] == "agent"
    ]
    assert len(agent_entries) == 1
    entry = agent_entries[0]
    assert entry["type"] == "agent.run"
    assert entry["agent_run_id"] == tenant360["run_a1_key"]
    assert entry["metadata"] == {"action": "respond", "status": "pending_approval"}
    blob = json_dumps(entry)
    for secret in (
        "Sensitive reasoning",
        "Sensitive draft",
        "Sensitive review note",
        "must never be exposed",
        "reason",
        "response_draft",
        "tool_plan",
        "sources",
        "workflow_path",
        "reviewer_note",
        "error_message",
    ):
        assert secret not in blob


@pytest.mark.asyncio
async def test_23_timeline_pagination_stable(make_client, tenant360):
    async with make_client() as client:
        r1 = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline",
            headers=_h_alpha(),
            params={"limit": 1},
        )
        r2 = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline",
            headers=_h_alpha(),
            params={"limit": 1},
        )
        r_page2 = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline",
            headers=_h_alpha(),
            params={"limit": 2, "offset": 2},
        )
    body1 = r1.json()
    body2 = r2.json()
    assert body1["total"] == 4
    assert len(body1["items"]) == 1
    assert body1["items"][0]["id"] == body2["items"][0]["id"]
    assert r_page2.json()["total"] == 4
    assert len(r_page2.json()["items"]) == 2


@pytest.mark.asyncio
async def test_24_timeline_order_has_no_unbounded_fanout(make_client, tenant360):
    # limit=50 stays within bounds and returns everything (4 events).
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline",
            headers=_h_alpha(),
            params={"limit": 100},
        )
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 4

    async with make_client() as client:
        r_bad = await client.get(
            f"/customers/{tenant360['cust_a_id']}/timeline",
            headers=_h_alpha(),
            params={"limit": 101},
        )
    assert r_bad.status_code == 422


@pytest.mark.asyncio
async def test_25_timeline_cross_tenant_404(make_client, tenant360):
    async with make_client() as client:
        r = await client.get(
            f"/customers/{tenant360['cust_b_id']}/timeline", headers=_h_alpha()
        )
    assert r.status_code == 404


# ===================================================================
# 26-29. Profile writes
# ===================================================================
@pytest.mark.asyncio
async def test_26_patch_updates_own_customer(make_client, tenant360):
    async with make_client() as client:
        r = await client.patch(
            f"/customers/{tenant360['cust_a_id']}",
            headers=_h_alpha(),
            json={
                "name": "Ada Renamed",
                "phone": "+1234567890",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ada Renamed"
    assert body["phone"] == "+1234567890"
    assert body["email"] == tenant360["email_alpha"]
    assert body["organization_id"] == tenant360["org_a_id"]


@pytest.mark.asyncio
async def test_27_patch_requires_customer_write(make_client, tenant360):
    # VIEWER lacks customer.write -> 403 with the standardized detail.
    async with make_client() as client:
        r = await client.patch(
            f"/customers/{tenant360['cust_a_id']}",
            headers=_h_viewer(),
            json={"name": "Should Not Apply"},
        )
    assert r.status_code == 403
    assert r.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_28_patch_duplicate_email_409_generic(make_client, tenant360):
    async with make_client() as client:
        r = await client.patch(
            f"/customers/{tenant360['cust_a_id']}",
            headers=_h_alpha(),
            json={"email": tenant360["email_alpha2"]},
        )
    assert r.status_code == 409
    assert r.json()["detail"] == "Customer resource conflict."


@pytest.mark.asyncio
async def test_29_patch_foreign_404_and_org_injection_rejected(make_client, tenant360):
    async with make_client() as client:
        r_foreign = await client.patch(
            f"/customers/{tenant360['cust_b_id']}",
            headers=_h_alpha(),
            json={"name": "Nope"},
        )
        r_inject = await client.patch(
            f"/customers/{tenant360['cust_a_id']}",
            headers=_h_alpha(),
            json={"organization_id": tenant360["org_b_id"]},
        )
    assert r_foreign.status_code == 404
    assert r_inject.status_code == 422


# ===================================================================
# 30. Org-switch isolation
# ===================================================================
@pytest.mark.asyncio
async def test_30_org_switch_never_leaks_previous_org(make_client, tenant360, db):
    # Grant alpha a second OWNER membership in Org B, then switch via selector.
    db.add(
        OrganizationMembership(
            subject=USER_ALPHA,
            organization_id=tenant360["org_b_id"],
            role=OrganizationRole.OWNER,
        )
    )
    await db.commit()

    headers = {
        **_h_alpha(),
        X_TENANT: str(tenant360["org_b_id"]),
    }

    async with make_client() as client:
        r_list = await client.get("/customers", headers=headers)
        r_detail = await client.get(
            f"/customers/{tenant360['cust_a_id']}", headers=headers
        )
        r_timeline = await client.get(
            f"/customers/{tenant360['cust_b_id']}/timeline", headers=headers
        )
        r_beta_anon = await client.get("/customers", headers=_h_beta())

    assert r_list.status_code == 200
    items, total = _envelope(r_list.json())
    assert total == 1
    assert items[0]["email"] == tenant360["email_beta"]
    assert items[0]["organization_id"] == tenant360["org_b_id"]

    # Org A customer is invisible under the Org B tenant.
    assert r_detail.status_code == 404

    # Org B timeline works under the switched tenant (its own customer).
    assert r_timeline.status_code == 200
    assert r_timeline.json()["total"] == 1

    # And the unswitched beta still sees only their own org.
    assert r_beta_anon.status_code == 200
    _, beta_total = _envelope(r_beta_anon.json())
    assert beta_total == 1

    # Cleanup: revoke the temporary membership.
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.subject == USER_ALPHA,
            OrganizationMembership.organization_id == tenant360["org_b_id"],
        )
    )
    await db.commit()


def json_dumps(value) -> str:
    import json

    return json.dumps(value)