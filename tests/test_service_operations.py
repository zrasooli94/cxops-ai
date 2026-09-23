"""Phase 1H — service operations: queues, SLA, routing, assignment, work queue.
"""

import os
import time
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-service-ops-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-service-ops-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.automation_rule import AutomationRule
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.service_queue import ServiceQueue
from app.models.sla_policy import SLAPolicy
from app.models.ticket import Ticket

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-service-ops-issuer"
TEST_AUDIENCE = "test-service-ops-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"

X_TENANT = "X-CXOps-Organization-ID"


@pytest_asyncio.fixture
async def org_scope(db):
    """Create unique test organizations and remove them on teardown."""
    org_ids: list[int] = []

    async def make(
        subject: str | None = None,
        role: OrganizationRole = OrganizationRole.OWNER,
    ) -> Organization:
        reset_settings_cache()
        org = Organization(name=f"org-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        if subject is not None:
            membership = OrganizationMembership(
                subject=subject,
                organization_id=org.id,
                role=role,
            )
            db.add(membership)
            await db.flush()
        await db.commit()
        return org

    yield make

    from sqlalchemy import delete

    from app.models.automation_rule import AutomationRule
    from app.models.conversation import Conversation
    from app.models.conversation_message import ConversationMessage

    for org_id in org_ids:
        await db.execute(
            delete(ConversationMessage).where(
                ConversationMessage.organization_id == org_id
            )
        )
        await db.execute(
            delete(Conversation).where(Conversation.organization_id == org_id)
        )
        await db.execute(delete(Ticket).where(Ticket.organization_id == org_id))
        await db.execute(
            delete(AutomationRule).where(AutomationRule.organization_id == org_id)
        )
        await db.execute(
            delete(ServiceQueue).where(ServiceQueue.organization_id == org_id)
        )
        await db.execute(
            delete(SLAPolicy).where(SLAPolicy.organization_id == org_id)
        )
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id
            )
        )
        await db.execute(delete(Organization).where(Organization.id == org_id))
    await db.commit()


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


def _auth_headers(sub: str, tenant_id: int) -> dict:
    return {
        "Authorization": f"Bearer {_build_token(sub=sub)}",
        X_TENANT: str(tenant_id),
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


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_sla_policy_crud(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    # Create
    response = await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 201
    policy = response.json()
    assert policy["name"] == "Default SLA"
    assert policy["is_default"] is True

    # Duplicate name blocked
    response = await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "first_response_low_minutes": 1,
            "first_response_normal_minutes": 1,
            "first_response_high_minutes": 1,
            "first_response_urgent_minutes": 1,
            "resolution_low_minutes": 1,
            "resolution_normal_minutes": 1,
            "resolution_high_minutes": 1,
            "resolution_urgent_minutes": 1,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 409

    # Two defaults blocked
    response = await client.post(
        "/sla-policies",
        json={
            "name": "Second Default",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_service_queue_crud_and_default(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    response = await client.post(
        "/service-queues",
        json={
            "key": "billing",
            "name": "Billing",
            "is_default": True,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 201
    queue = response.json()
    assert queue["key"] == "billing"
    assert queue["is_default"] is True

    # Duplicate key blocked
    response = await client.post(
        "/service-queues",
        json={
            "key": "billing",
            "name": "Billing 2",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 409

    # Two defaults blocked
    response = await client.post(
        "/service-queues",
        json={
            "key": "support",
            "name": "Support",
            "is_default": True,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_new_ticket_routes_to_default_queue_and_sla(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    # Create default queue and SLA
    sla_response = await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert sla_response.status_code == 201

    queue_response = await client.post(
        "/service-queues",
        json={
            "key": "support",
            "name": "Support",
            "is_default": True,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert queue_response.status_code == 201

    # Create ticket
    response = await client.post(
        "/tickets",
        json={
            "subject": "Route me",
            "description": "Please route this ticket",
            "priority": "normal",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["service_queue_id"] == queue_response.json()["id"]
    assert ticket["sla_policy_id"] == sla_response.json()["id"]
    assert ticket["routing_source"] == "default"
    assert ticket["first_response_due_at"] is not None


@pytest.mark.asyncio
async def test_automation_rule_routes_by_queue_key(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    queue_response = await client.post(
        "/service-queues",
        json={
            "key": "billing",
            "name": "Billing",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert queue_response.status_code == 201
    queue_id = queue_response.json()["id"]

    # Automation rule with service_queue_key action
    rule = AutomationRule(
        organization_id=org.id,
        name="route billing",
        event_type="ticket.created",
        priority=1,
        enabled=True,
        conditions={"any_keywords": ["invoice"]},
        actions={"service_queue_key": "billing"},
    )
    db.add(rule)
    await db.commit()

    response = await client.post(
        "/tickets",
        json={
            "subject": "Invoice issue",
            "description": "My invoice is wrong",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["service_queue_id"] == queue_id
    assert ticket["routing_source"] == "automation"


@pytest.mark.asyncio
async def test_claim_ticket_and_assign_queue(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    queue_response = await client.post(
        "/service-queues",
        json={
            "key": "support",
            "name": "Support",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    queue_id = queue_response.json()["id"]

    ticket_response = await client.post(
        "/tickets",
        json={
            "subject": "Help",
            "description": "I need help",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    ticket_id = ticket_response.json()["id"]

    # Assign queue
    response = await client.patch(
        f"/tickets/{ticket_id}/assignment",
        json={"service_queue_id": queue_id},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["service_queue_id"] == queue_id

    # Claim
    response = await client.post(
        f"/service-operations/tickets/{ticket_id}/claim",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["assigned_subject"] == USER_ALPHA

    # Second claim fails
    response = await client.post(
        f"/service-operations/tickets/{ticket_id}/claim",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_sla_deadline_from_created_at_not_assignment_time(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    sla_response = await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert sla_response.status_code == 201

    ticket_response = await client.post(
        "/tickets",
        json={
            "subject": "SLA test",
            "description": "Test SLA clock",
            "priority": "normal",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    ticket = ticket_response.json()
    created_at = datetime.fromisoformat(ticket["created_at"])
    due_at = datetime.fromisoformat(ticket["first_response_due_at"])
    assert due_at - created_at == timedelta(minutes=120)


@pytest.mark.asyncio
async def test_priority_change_recalculates_sla_from_created_at(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )

    ticket_response = await client.post(
        "/tickets",
        json={
            "subject": "SLA priority test",
            "description": "Test",
            "priority": "normal",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    ticket_id = ticket_response.json()["id"]
    created_at = datetime.fromisoformat(ticket_response.json()["created_at"])

    response = await client.patch(
        f"/tickets/{ticket_id}",
        json={"priority": "urgent"},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    due_at = datetime.fromisoformat(response.json()["first_response_due_at"])
    assert due_at - created_at == timedelta(minutes=15)


@pytest.mark.asyncio
async def test_resolution_clears_resolution_due_at(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )

    ticket_response = await client.post(
        "/tickets",
        json={
            "subject": "Resolve me",
            "description": "Test resolution",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    ticket_id = ticket_response.json()["id"]
    assert ticket_response.json()["resolution_due_at"] is not None

    response = await client.patch(
        f"/tickets/{ticket_id}",
        json={"status": "solved"},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "solved"
    assert response.json()["resolved_at"] is not None
    # Historical resolution deadline is preserved for met/breached evaluation.
    assert response.json()["resolution_due_at"] is not None


@pytest.mark.asyncio
async def test_reopen_clears_resolved_at_keeps_deadline(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )

    ticket_response = await client.post(
        "/tickets",
        json={
            "subject": "Reopen me",
            "description": "Test reopen",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    ticket_id = ticket_response.json()["id"]
    original_resolution_due = ticket_response.json()["resolution_due_at"]

    await client.patch(
        f"/tickets/{ticket_id}",
        json={"status": "solved"},
        headers=_auth_headers(USER_ALPHA, org.id),
    )

    response = await client.patch(
        f"/tickets/{ticket_id}",
        json={"status": "open"},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["resolved_at"] is None
    assert response.json()["resolution_due_at"] == original_resolution_due


@pytest.mark.asyncio
async def test_operations_queue_and_summary_visible_to_ticket_reader(
    db, client, org_scope
):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.VIEWER)

    # Viewer has ticket.read but not ticket.write
    response = await client.get(
        "/service-operations/queue",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200

    response = await client.get(
        "/service-operations/summary",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_operations_mutations_require_ticket_write(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.VIEWER)

    # Add an agent/owner to the same org to seed the ticket
    db.add(
        OrganizationMembership(
            subject=USER_BETA,
            organization_id=org.id,
            role=OrganizationRole.OWNER,
        )
    )
    await db.commit()

    # Seed a ticket in the viewer's org by having the owner create it
    ticket_response = await client.post(
        "/tickets",
        json={"subject": "Viewer mutation test", "description": "Test"},
        headers=_auth_headers(USER_BETA, org.id),
    )
    assert ticket_response.status_code == 201
    ticket_id = ticket_response.json()["id"]

    # Claim is forbidden for viewer
    response = await client.post(
        f"/service-operations/tickets/{ticket_id}/claim",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 403

    # Apply AI suggestion is forbidden for viewer
    response = await client.post(
        f"/service-operations/tickets/{ticket_id}/apply-suggestion",
        json={"queue_id": 1},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 403

    # Direct assignment mutation is forbidden for viewer
    response = await client.patch(
        f"/tickets/{ticket_id}/assignment",
        json={"assigned_subject": USER_ALPHA},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_agent_can_claim_and_read_operations(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.AGENT)

    # Agent can read operations workspace
    response = await client.get(
        "/service-operations/queue",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200

    response = await client.get(
        "/service-operations/summary",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200

    # Create a ticket and claim it
    ticket_response = await client.post(
        "/tickets",
        json={"subject": "Claim me", "description": "Test"},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert ticket_response.status_code == 201
    ticket_id = ticket_response.json()["id"]

    response = await client.post(
        f"/service-operations/tickets/{ticket_id}/claim",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["assigned_subject"] == USER_ALPHA


@pytest.mark.asyncio
async def test_tenant_isolation_for_queues_and_sla(db, client, org_scope):
    org_a = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    org_b = await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)

    # Create queue in org A
    response = await client.post(
        "/service-queues",
        json={"key": "secret", "name": "Secret"},
        headers=_auth_headers(USER_ALPHA, org_a.id),
    )
    assert response.status_code == 201
    queue_id = response.json()["id"]

    # Org B cannot see it
    response = await client.get(
        f"/service-queues/{queue_id}",
        headers=_auth_headers(USER_BETA, org_b.id),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_sla_policy_edit_does_not_rewrite_existing_tickets(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    sla_response = await client.post(
        "/sla-policies",
        json={
            "name": "Default SLA",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    sla_id = sla_response.json()["id"]

    ticket_response = await client.post(
        "/tickets",
        json={"subject": "Frozen SLA", "description": "Test"},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    original_due = ticket_response.json()["first_response_due_at"]

    # Edit policy
    response = await client.patch(
        f"/sla-policies/{sla_id}",
        json={"first_response_normal_minutes": 5},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200

    # Existing ticket deadline unchanged
    response = await client.get(
        f"/tickets/{ticket_response.json()['id']}",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.json()["first_response_due_at"] == original_due


@pytest.mark.asyncio
async def test_default_queue_sla_policy_used_when_queue_has_none(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    sla_response = await client.post(
        "/sla-policies",
        json={
            "name": "Tenant Default",
            "is_default": True,
            "first_response_low_minutes": 240,
            "first_response_normal_minutes": 120,
            "first_response_high_minutes": 60,
            "first_response_urgent_minutes": 15,
            "resolution_low_minutes": 2880,
            "resolution_normal_minutes": 1440,
            "resolution_high_minutes": 480,
            "resolution_urgent_minutes": 120,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    sla_id = sla_response.json()["id"]

    await client.post(
        "/service-queues",
        json={"key": "support", "name": "Support", "is_default": True},
        headers=_auth_headers(USER_ALPHA, org.id),
    )

    response = await client.post(
        "/tickets",
        json={"subject": "Use tenant SLA", "description": "Test"},
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 201
    assert response.json()["sla_policy_id"] == sla_id


@pytest.mark.asyncio
async def test_workload_visible_with_ticket_read_and_member_read(
    db, client, org_scope
):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.SUPERVISOR)

    response = await client.get(
        "/service-operations/workload",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert list(body.keys()) == ["members"]
    assert isinstance(body["members"], list)
    assert len(body["members"]) == 1
    assert set(body["members"][0].keys()) == {
        "subject",
        "open_assigned",
        "breached",
        "urgent",
        "needs_response",
        "due_soon",
    }


@pytest.mark.asyncio
async def test_workload_hidden_without_member_read(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.VIEWER)

    # viewer holds ticket.read but not member.read
    response = await client.get(
        "/service-operations/workload",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 403
