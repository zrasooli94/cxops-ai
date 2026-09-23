"""Phase 1I — SLA escalation lifecycle: scanner, durable job, transitions,
acknowledgment, tenant isolation, and resolution-cycle invariants.

These tests pin the read/write capability gates and the scanner→job→transition
pipeline that the release gate requires to be proven by executed tests:

- the scanner only enqueues durable, tenant-bound, deduplicated jobs
- job execution re-validates the ticket before persisting anything (item 8)
- due_soon supersedes to breached on the same row with one event per transition
- acknowledge requires ticket.write, is tenant-isolated, and is idempotent
- escalation list/summary require only ticket.read and never leak a foreign
  tenant's rows
- reopening a resolved ticket starts a NEW resolution SLA cycle that actually
  re-escalates (cycle 0 -> 1)
"""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-sla-esc-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-sla-esc-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from sqlalchemy import delete, select

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.automation_rule import AutomationRule
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.service_escalation import ServiceEscalation
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from app.services.integration_job_service import IntegrationJobService
from app.services.service_escalation_service import ServiceEscalationService
from app.services.sla_escalation_scanner_service import (
    SLA_ESCALATION_JOB_TYPE,
    SLAEscalationScannerService,
)

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-sla-esc-issuer"
TEST_AUDIENCE = "test-sla-esc-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"
USER_NOBODY = "user-nobody"

X_TENANT = "X-CXOps-Organization-ID"


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


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def org_scope(db):
    """Unique test organizations, removed on teardown (escalation children first)."""
    org_ids: list[int] = []

    async def make(
        subject: str | None = None,
        role: OrganizationRole = OrganizationRole.OWNER,
    ) -> Organization:
        reset_settings_cache()
        org = Organization(name=f"sla-org-{uuid.uuid4().hex[:8]}")
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
            await db.flush()
        await db.commit()
        return org

    yield make

    for org_id in org_ids:
        await db.execute(
            delete(IntegrationJob).where(IntegrationJob.organization_id == org_id)
        )
        await db.execute(
            delete(ServiceEscalation).where(
                ServiceEscalation.organization_id == org_id
            )
        )
        await db.execute(
            delete(AutomationRule).where(
                AutomationRule.organization_id == org_id
            )
        )
        ticket_ids = list(
            (
                await db.execute(
                    select(Ticket.id).where(Ticket.organization_id == org_id)
                )
            ).scalars().all()
        )
        if ticket_ids:
            await db.execute(
                delete(TicketEvent).where(TicketEvent.ticket_id.in_(ticket_ids))
            )
        await db.execute(delete(Ticket).where(Ticket.organization_id == org_id))
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id
            )
        )
        await db.execute(delete(Organization).where(Organization.id == org_id))
    await db.commit()


async def _make_ticket(
    db,
    organization_id: int,
    *,
    first_response_due_at: datetime | None = None,
    resolution_due_at: datetime | None = None,
    resolution_sla_cycle: int = 0,
) -> Ticket:
    ticket = Ticket(
        organization_id=organization_id,
        subject=f"sla-esc-{uuid.uuid4().hex[:8]}",
        description="sla escalation test ticket",
        first_response_due_at=first_response_due_at,
        resolution_due_at=resolution_due_at,
        resolution_sla_cycle=resolution_sla_cycle,
    )
    db.add(ticket)
    await db.flush()
    return ticket


async def _escalations_for(
    db, *, organization_id: int | None = None, ticket_id: int | None = None
) -> list[ServiceEscalation]:
    stmt = select(ServiceEscalation).order_by(ServiceEscalation.id)
    if organization_id is not None:
        stmt = stmt.where(ServiceEscalation.organization_id == organization_id)
    if ticket_id is not None:
        stmt = stmt.where(ServiceEscalation.ticket_id == ticket_id)
    return list((await db.execute(stmt)).scalars().all())


async def _events_for(db, escalation: ServiceEscalation) -> list[TicketEvent]:
    stmt = (
        select(TicketEvent)
        .where(TicketEvent.payload["escalation_id"].as_integer() == escalation.id)
        .order_by(TicketEvent.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _sla_jobs(db, *, organization_id: int | None = None) -> list[IntegrationJob]:
    stmt = select(IntegrationJob).where(
        IntegrationJob.job_type == SLA_ESCALATION_JOB_TYPE
    )
    if organization_id is not None:
        stmt = stmt.where(IntegrationJob.organization_id == organization_id)
    stmt = stmt.order_by(IntegrationJob.id)
    return list((await db.execute(stmt)).scalars().all())


async def _drive_escalation(
    db, *, organization_id: int, ticket_id: int, now: datetime
) -> ServiceEscalation:
    """Scan + execute for THIS ticket; returns the resulting escalation.

    The scanner is global (worker-only) and may enqueue jobs for other
    tenants' tickets in the same cycle, so the right job is picked by ticket id.

    Takes a plain id, not a Ticket: the unique-violation rollback inside
    execute() expires every ORM object in the session, so touching ``ticket``
    after execute would raise MissingGreenlet.
    """
    evaluated, _ = await SLAEscalationScannerService.scan_once(db, now=now)
    assert evaluated >= 1
    jobs = await _sla_jobs(db, organization_id=organization_id)
    job = [j for j in jobs if j.payload.get("ticket_id") == ticket_id][-1]
    await IntegrationJobService.execute(db, job)
    escalations = await _escalations_for(db, ticket_id=ticket_id)
    assert len(escalations) == 1
    return escalations[0]


async def _reload(db, obj):
    await db.refresh(obj)
    return obj


@pytest.mark.asyncio
async def test_scanner_enqueues_durable_tenant_bound_job_then_execute(
    db, client, org_scope
):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    now = datetime.now(UTC)
    ticket = await _make_ticket(
        db,
        org.id,
        first_response_due_at=now - timedelta(minutes=5),
    )
    await db.commit()

    evaluated, enqueued = await SLAEscalationScannerService.scan_once(db, now=now)
    assert evaluated == 1
    assert enqueued == 1
    assert await _escalations_for(db, organization_id=org.id) == []

    jobs = await _sla_jobs(db, organization_id=org.id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.organization_id == org.id
    assert job.status == "pending"
    assert job.payload == {
        "ticket_id": ticket.id,
        "milestone": "first_response",
        "stage": "breached",
        "sla_cycle": 0,
        "due_at": ticket.first_response_due_at.isoformat(),
    }
    assert job.dedupe_key == (
        f"sla-escalation:{org.id}:{ticket.id}:first_response:breached:0:"
        f"{int(ticket.first_response_due_at.timestamp())}"
    )

    await IntegrationJobService.execute(db, job)

    escalation = (await _escalations_for(db, organization_id=org.id))[0]
    assert escalation.milestone == "first_response"
    assert escalation.stage == "breached"
    assert escalation.status == "open"
    assert escalation.resolution_sla_cycle == 0
    assert escalation.transition_version == 1
    assert escalation.event_key == (
        f"sla:{org.id}:{ticket.id}:first_response:breached:0:"
        f"{int(ticket.first_response_due_at.timestamp())}"
    )

    events = await _events_for(db, escalation)
    assert len(events) == 1
    assert events[0].event_type == "sla.first_response.breached"
    assert events[0].ticket_id == ticket.id
    # execute() immediately feeds the transition event to the automation
    # pipeline, which marks it processed (no matching rules -> no-op).
    assert events[0].processed is True


@pytest.mark.asyncio
async def test_scanner_and_execute_are_idempotent(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    now = datetime.now(UTC)
    ticket = await _make_ticket(
        db, org.id, first_response_due_at=now - timedelta(minutes=5)
    )
    await db.commit()

    await _drive_escalation(db, organization_id=org.id, ticket_id=ticket.id, now=now)

    # A second scan must NOT enqueue a duplicate job for the same trigger.
    evaluated, enqueued = await SLAEscalationScannerService.scan_once(db, now=now)
    assert evaluated == 1
    assert enqueued == 0

    # A worker retry of the same job stays deterministic: same single
    # escalation row and the same single transition event.
    job = (await _sla_jobs(db, organization_id=org.id))[0]
    await IntegrationJobService.execute(db, job)

    escalations = await _escalations_for(db, organization_id=org.id)
    assert len(escalations) == 1
    assert escalations[0].transition_version == 1
    assert len(await _events_for(db, escalations[0])) == 1


@pytest.mark.asyncio
async def test_stale_sla_job_revalidation_is_a_noop(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    now = datetime.now(UTC)
    ticket = await _make_ticket(
        db, org.id, first_response_due_at=now - timedelta(minutes=5)
    )
    await db.commit()

    async def _run_with(**payload_overrides) -> None:
        payload = {
            "ticket_id": ticket.id,
            "milestone": "first_response",
            "stage": "breached",
            "sla_cycle": 0,
            "due_at": ticket.first_response_due_at.isoformat(),
        }
        payload.update(payload_overrides)
        job = IntegrationJob(
            organization_id=org.id,
            dedupe_key=f"test-stale-{uuid.uuid4().hex}",
            job_type=SLA_ESCALATION_JOB_TYPE,
            payload=payload,
        )
        db.add(job)
        await db.commit()
        await IntegrationJobService.execute(db, job)

    # Stale due_at: the deadline moved after the job was enqueued.
    await _run_with(due_at=(now - timedelta(days=1)).isoformat())

    # Wrong organization binding: this ticket belongs to the OTHER tenant, so
    # the job reads nothing and must not persist any escalation.
    other = await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)
    foreign_ticket = await _make_ticket(
        db, other.id, first_response_due_at=now - timedelta(minutes=5)
    )
    await db.commit()
    foreign_job = IntegrationJob(
        organization_id=org.id,
        dedupe_key=f"test-foreign-{uuid.uuid4().hex}",
        job_type=SLA_ESCALATION_JOB_TYPE,
        payload={
            "ticket_id": foreign_ticket.id,
            "milestone": "first_response",
            "stage": "breached",
            "sla_cycle": 0,
            "due_at": foreign_ticket.first_response_due_at.isoformat(),
        },
    )
    db.add(foreign_job)
    await db.commit()
    await IntegrationJobService.execute(db, foreign_job)

    assert await _escalations_for(db, organization_id=org.id) == []
    assert await _escalations_for(db, organization_id=other.id) == []

    # Completed milestone: the first response landed after the job was enqueued.
    ticket = (
        await db.execute(select(Ticket).where(Ticket.id == ticket.id))
    ).scalar_one()
    ticket.first_response_at = now
    await db.commit()
    await _run_with()
    assert await _escalations_for(db, organization_id=org.id) == []


@pytest.mark.asyncio
async def test_due_soon_supersedes_to_breached_on_same_row(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    now = datetime.now(UTC)
    ticket = await _make_ticket(
        db,
        org.id,
        first_response_due_at=now + timedelta(minutes=25),
    )
    await db.commit()

    due_soon_esc = await _drive_escalation(
        db, organization_id=org.id, ticket_id=ticket.id, now=now
    )
    first_id = due_soon_esc.id
    assert due_soon_esc.stage == "due_soon"
    assert due_soon_esc.transition_version == 1
    events = await _events_for(db, due_soon_esc)
    assert [e.event_type for e in events] == ["sla.first_response.due_soon"]

    # The ticket breaches against the real clock.
    breached_now = datetime.now(UTC)
    ticket2 = (
        await db.execute(select(Ticket).where(Ticket.id == ticket.id))
    ).scalar_one()
    ticket2.first_response_due_at = breached_now - timedelta(minutes=1)
    await db.commit()

    _, enqueued = await SLAEscalationScannerService.scan_once(
        db, now=breached_now
    )
    assert enqueued == 1

    # The breach job comes AFTER the still-pending due_soon job in the queue.
    breach_job = (await _sla_jobs(db, organization_id=org.id))[1]
    assert breach_job.payload["stage"] == "breached"
    await IntegrationJobService.execute(db, breach_job)

    # Same single row: due_soon was superseded, not duplicated.
    escalations = await _escalations_for(db, organization_id=org.id)
    assert len(escalations) == 1
    escalation = escalations[0]
    assert escalation.id == first_id
    assert escalation.stage == "breached"
    assert escalation.status == "open"
    assert escalation.resolved_at is None
    assert escalation.resolution_reason is None
    # create (v1) -> resolve due_soon (v2) -> reopen breached (v3)
    assert escalation.transition_version == 3

    events = await _events_for(db, escalation)
    assert [e.event_type for e in events] == [
        "sla.first_response.due_soon",
        "sla.escalation.resolved",
        "sla.first_response.breached",
    ]
    # One event per transition, keyed by the escalation's version
    # (TicketEvent itself carries no version column).
    assert sorted(e.payload["transition_version"] for e in events) == [1, 2, 3]

    # Re-executing the breach job leaves the state untouched (worker retry).
    await IntegrationJobService.execute(db, breach_job)
    assert len(await _events_for(db, escalation)) == 3
    reloaded = await _reload(db, escalation)
    assert reloaded.transition_version == 3


@pytest.mark.asyncio
async def test_acknowledge_requires_ticket_write_and_is_idempotent(
    db, client, org_scope
):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    db.add(
        OrganizationMembership(
            subject=USER_BETA,
            organization_id=org.id,
            role=OrganizationRole.VIEWER,
        )
    )
    await db.commit()

    now = datetime.now(UTC)
    ticket = await _make_ticket(
        db, org.id, first_response_due_at=now - timedelta(minutes=5)
    )
    await db.commit()
    escalation = await _drive_escalation(
        db, organization_id=org.id, ticket_id=ticket.id, now=now
    )

    # Viewer: ticket.read only -> 403.
    response = await client.post(
        f"/service-operations/escalations/{escalation.id}/acknowledge",
        headers=_auth_headers(USER_BETA, org.id),
    )
    assert response.status_code == 403
    reloaded = await _reload(db, escalation)
    assert reloaded.status == "open"

    # Owner: acknowledge succeeds.
    response = await client.post(
        f"/service-operations/escalations/{escalation.id}/acknowledge",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "acknowledged"
    assert body["acknowledged_by_subject"] == USER_ALPHA
    assert body["acknowledged_at"] is not None

    reloaded = await _reload(db, escalation)
    assert reloaded.status == "acknowledged"
    version_after_first = reloaded.transition_version
    acked_at_after_first = reloaded.acknowledged_at

    # A second acknowledge is a no-op: same row, same version, single event.
    response = await client.post(
        f"/service-operations/escalations/{escalation.id}/acknowledge",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    reloaded = await _reload(db, escalation)
    assert reloaded.transition_version == version_after_first
    assert reloaded.acknowledged_at == acked_at_after_first
    acked_events = [
        e
        for e in await _events_for(db, reloaded)
        if e.event_type == "sla.escalation.acknowledged"
    ]
    assert len(acked_events) == 1


@pytest.mark.asyncio
async def test_acknowledge_foreign_tenant_escalation_404(db, client, org_scope):
    org_a = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    org_b = await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)
    now = datetime.now(UTC)
    ticket_b = await _make_ticket(
        db, org_b.id, first_response_due_at=now - timedelta(minutes=5)
    )
    await db.commit()
    esc_b = await _drive_escalation(
db, organization_id=org_b.id, ticket_id=ticket_b.id, now=now
    )

    response = await client.post(
        f"/service-operations/escalations/{esc_b.id}/acknowledge",
        headers=_auth_headers(USER_ALPHA, org_a.id),
    )
    assert response.status_code == 404

    reloaded = (
        await db.execute(
            select(ServiceEscalation).where(ServiceEscalation.id == esc_b.id)
        )
    ).scalar_one()
    await db.refresh(reloaded)
    assert reloaded.status == "open"


@pytest.mark.asyncio
async def test_escalation_list_and_summary_ticket_read_and_tenant_isolation(
    db, client, org_scope
):
    org_a = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    org_b = await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)
    db.add(
        OrganizationMembership(
            subject=USER_NOBODY,
            organization_id=org_a.id,
            role=OrganizationRole.VIEWER,
        )
    )
    await db.commit()

    now = datetime.now(UTC)
    ticket_a = await _make_ticket(
        db, org_a.id, first_response_due_at=now - timedelta(minutes=5)
    )
    ticket_b = await _make_ticket(
        db, org_b.id, first_response_due_at=now - timedelta(minutes=10)
    )
    await db.commit()
    esc_a = await _drive_escalation(
        db, organization_id=org_a.id, ticket_id=ticket_a.id, now=now
    )
    await _drive_escalation(db, organization_id=org_b.id, ticket_id=ticket_b.id, now=now)

    # Viewer (ticket.read only) can list and read the summary.
    list_response = await client.get(
        "/service-operations/escalations",
        headers=_auth_headers(USER_NOBODY, org_a.id),
    )
    assert list_response.status_code == 200
    items = list_response.json()
    assert [item["id"] for item in items] == [esc_a.id]
    assert items[0]["subject"] == ticket_a.subject
    assert items[0]["milestone"] == "first_response"
    assert items[0]["stage"] == "breached"
    assert items[0]["resolution_sla_cycle"] == 0
    assert all(item["ticket_id"] == ticket_a.id for item in items)

    summary = await client.get(
        "/service-operations/escalations/summary",
        headers=_auth_headers(USER_NOBODY, org_a.id),
    )
    assert summary.status_code == 200
    assert summary.json() == {
        "total": 1,
        "active": 1,
        "unacknowledged": 1,
        "due_soon": 0,
        "breached": 1,
    }

    # The other tenant sees only its own escalation.
    other_summary = await client.get(
        "/service-operations/escalations/summary",
        headers=_auth_headers(USER_BETA, org_b.id),
    )
    assert other_summary.status_code == 200
    assert other_summary.json()["total"] == 1

    # A non-member has no tenant to read from.
    response = await client.get(
        "/service-operations/escalations/summary",
        headers=_auth_headers("user-unrelated", org_a.id),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_resolution_cycle_reescalates_after_reopen(db, client, org_scope):
    """A reopened ticket must materialize a NEW resolution SLA cycle.

    The (organization, ticket, milestone) unique constraint keeps ONE
    escalation row per milestone across the whole ticket life, so the cycle-1
    re-escalation recycles the resolved cycle-0 row rather than inserting a
    second row — and must stamp the new cycle onto it.
    """
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    now = datetime.now(UTC)
    ticket = await _make_ticket(
        db,
        org.id,
        resolution_due_at=now - timedelta(minutes=5),
        resolution_sla_cycle=0,
    )
    await db.commit()

    cycle0 = await _drive_escalation(db, organization_id=org.id, ticket_id=ticket.id, now=now)
    assert cycle0.milestone == "resolution"
    assert cycle0.stage == "breached"
    assert cycle0.resolution_sla_cycle == 0

    # Resolve the cycle-0 breach the way a ticket solve does, then reopen the
    # ticket: cycle bumps to 1 and handle_reopen recalculates the deadline
    # (which, for this old ticket, lands back in the past -> breach again).
    ticket.status = "solved"
    ticket.resolved_at = now
    await db.commit()
    await ServiceEscalationService.resolve_for_milestone_completion(
        db,
        ticket=ticket,
        milestone="resolution",
        now=now,
    )
    await db.commit()

    reopened_now = datetime.now(UTC)
    ticket2 = (
        await db.execute(select(Ticket).where(Ticket.id == ticket.id))
    ).scalar_one()
    ticket2.status = "open"
    ticket2.resolved_at = None
    ticket2.resolution_sla_cycle += 1
    ticket2.resolution_due_at = reopened_now - timedelta(minutes=1)
    await db.commit()

    cycle1 = await _drive_escalation(
        db, organization_id=org.id, ticket_id=ticket2.id, now=reopened_now
    )

    # Same single row, now on cycle 1.
    assert cycle1.id == cycle0.id
    assert cycle1.resolution_sla_cycle == 1
    assert cycle1.stage == "breached"
    assert cycle1.status == "open"

    events = await _events_for(db, cycle1)
    assert "sla.escalation.resolved" in [e.event_type for e in events]
    assert "sla.resolution.breached" in [e.event_type for e in events]


@pytest.mark.asyncio
async def test_sla_automation_rule_action_safety(client, org_scope):
    """SLA escalation automation rules cannot mutate arbitrary ticket fields.

    action keys are whitelisted to {priority, service_queue_key, category};
    anything else is rejected with 400 even for an AUTOMATION_MANAGE holder.
    """
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    headers = _auth_headers(USER_ALPHA, org.id)

    # A disallowed action field is rejected.
    response = await client.post(
        "/automation-rules",
        json={
            "name": "auto-assign breach",
            "event_type": "sla.first_response.breached",
            "actions": {"assignee_subject": "user-x"},
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]

    # An out-of-range SLA priority is rejected.
    response = await client.post(
        "/automation-rules",
        json={
            "name": "bad priority",
            "event_type": "sla.resolution.breached",
            "actions": {"priority": "p0"},
        },
        headers=headers,
    )
    assert response.status_code == 400

    # Whitelisted actions persist and are tenant-scoped.
    response = await client.post(
        "/automation-rules",
        json={
            "name": "escalate breach",
            "event_type": "sla.first_response.breached",
            "actions": {
                "priority": "urgent",
                "service_queue_key": "tier1",
                "category": "escalation",
            },
        },
        headers=headers,
    )
    assert response.status_code == 201
    rule_id = response.json()["id"]

    listing = await client.get("/automation-rules", headers=headers)
    assert listing.status_code == 200
    assert [r["id"] for r in listing.json()] == [rule_id]

    # A non-member holder of the token has no tenant -> denied.
    other = await client.get(
        "/automation-rules", headers=_auth_headers("user-unrelated", org.id)
    )
    assert other.status_code == 403