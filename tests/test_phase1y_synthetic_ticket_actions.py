"""Phase 1Y: synthetic-only ticket actions in disposable PostgreSQL and ASGI.

Production auth semantics are tested elsewhere. Here a synthetic verified
Neon principal is injected through FastAPI's auth seam while the REAL durable
tenant membership and capability checks execute against disposable Postgres.

No Neon, OpenAI, Zendesk, email, Telegram, or production DB connection occurs.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import main
from app.api.deps import get_current_principal_dep
from app.api.routes import staging_ticket_action as action_route
from app.core.principal import AuthenticatedPrincipal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.ai_request_log import AIRequestLog
from app.models.customer import Customer
from app.models.integration_job import IntegrationJob
from app.models.ticket import Ticket
from staging.http_gate import staging_request_allowed
from tests import test_staff_public_chat_resolve as base

# Follow the project's established disposable-DB namespace/teardown fixtures.
_configure_auth = base._configure_auth
_p1p7r_namespace_isolated = base._p1p7r_namespace_isolated
client = base.client
db = base.db
org_scope = base.org_scope

ACTOR = "00000000-0000-4000-8000-000000001001"
VIEWER = "00000000-0000-4000-8000-000000001002"


@pytest.fixture
def staging_gate(monkeypatch):
    monkeypatch.setenv("CXOPS_STAGING_TICKET_ACTIONS_ENABLED", "true")
    monkeypatch.setenv("CXOPS_STAGING_TICKET_AGENT_SUBJECT", ACTOR)
    monkeypatch.setattr(
        main, "settings", SimpleNamespace(environment="staging")
    )
    monkeypatch.setattr(
        action_route, "settings",
        SimpleNamespace(
            environment="staging",
            auth_mode="neon_staging",
            onboarding_knowledge_setup_enabled=False,
        ),
    )
    def provide(subject: str):
        app.dependency_overrides[get_current_principal_dep] = (
            lambda: AuthenticatedPrincipal(
                subject=subject,
                email="synthetic.agent@example.test",
                issuer="https://isolated-staging.example.test",
                auth_method="neon_staging_ed25519",
            )
        )
    provide(ACTOR)
    yield provide
    app.dependency_overrides.pop(get_current_principal_dep, None)


async def _make_ticket(db, org_id: int, *, external_id: str, source: str = "synthetic_staging"):
    ticket = Ticket(
        organization_id=org_id,
        external_id=external_id,
        subject="Synthetic: test ticket",
        description="Fictional staging-only test",
        status="open",
        priority="normal",
        source=source,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def _get_ticket(db, id_: int):
    return (
        await db.execute(
            select(Ticket).where(Ticket.id == id_).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _setup(db, org_scope):
    alpha = await org_scope()
    alpha.name = "Synthetic Alpha Workshop"
    await db.commit()
    beta = await org_scope()
    beta.name = "Synthetic Beta Workshop"
    await db.commit()
    await base._add_membership(db, ACTOR, alpha.id, role=OrganizationRole.AGENT)
    await base._add_membership(db, VIEWER, alpha.id, role=OrganizationRole.VIEWER)
    return alpha, beta


def _headers(org_id: int):
    return {"X-CXOps-Organization-ID": str(org_id)}


def _action(ticket: Ticket, action: str, *, version=None):
    return {
        "ticket_id": ticket.id,
        "action": action,
        "expected_version": (version or ticket.updated_at).isoformat(),
    }


@pytest.mark.asyncio
async def test_synthetic_staging_claim_release_resolve_updates_exactly_one_ticket(
    db, client, org_scope, staging_gate,
):
    alpha, beta = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T-TEST")
    other = await _make_ticket(db, beta.id, external_id="PHASE1X-BETA-T-TEST")
    endpoint = "/staging/ticket-action"

    before = (
        (await db.execute(select(func.count()).select_from(IntegrationJob))).scalar_one(),
        (await db.execute(select(func.count()).select_from(AIRequestLog))).scalar_one(),
    )
    result = await client.post(
        endpoint, json=_action(ticket, "claim"),
        headers=_headers(alpha.id),
    )
    assert result.status_code == 200, result.text
    assert result.json()["assigned_to_you"] is True
    assert (await _get_ticket(db, ticket.id)).assigned_subject == ACTOR

    claimed_version = result.json()["version"]
    repeat = await client.post(endpoint, json=_action(ticket, "claim", version=ticket.updated_at), headers=_headers(alpha.id))
    assert repeat.status_code == 409

    release = await client.post(endpoint, json={**_action(ticket, "release"), "expected_version": claimed_version}, headers=_headers(alpha.id))
    assert release.status_code == 200
    assert release.json()["assigned_to_you"] is False

    released_version = release.json()["version"]
    premature = await client.post(endpoint, json={**_action(ticket, "resolve"), "expected_version": released_version}, headers=_headers(alpha.id))
    assert premature.status_code == 409

    reassign = await client.post(endpoint, json={**_action(ticket, "claim"), "expected_version": released_version}, headers=_headers(alpha.id))
    assert reassign.status_code == 200
    solved = await client.post(endpoint, json={**_action(ticket, "resolve"), "expected_version": reassign.json()["version"]}, headers=_headers(alpha.id))
    assert solved.status_code == 200
    assert solved.json()["status"] == "solved"
    assert solved.json()["resolved_at"]
    assert (await _get_ticket(db, ticket.id)).resolved_at is not None

    stale = await client.post(endpoint, json={**_action(ticket, "resolve"), "expected_version": reassign.json()["version"]}, headers=_headers(alpha.id))
    assert stale.status_code == 409
    assert (await _get_ticket(db, other.id)).status == "open"
    after = (
        (await db.execute(select(func.count()).select_from(IntegrationJob))).scalar_one(),
        (await db.execute(select(func.count()).select_from(AIRequestLog))).scalar_one(),
    )
    assert after == before


@pytest.mark.asyncio
async def test_fails_closed_for_viewer_foreign_org_and_non_synthetic_ticket(
    db, client, org_scope, staging_gate,
):
    alpha, beta = await _setup(db, org_scope)
    alpha_ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T2")
    beta_ticket = await _make_ticket(db, beta.id, external_id="PHASE1X-BETA-T2")
    non_synthetic = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T3", source="zendesk")
    endpoint = "/staging/ticket-action"

    staging_gate(VIEWER)
    viewer = await client.post(endpoint, json=_action(alpha_ticket, "claim"), headers=_headers(alpha.id))
    assert viewer.status_code == 403
    staging_gate(ACTOR)

    foreign = await client.post(endpoint, json=_action(beta_ticket, "claim"), headers=_headers(alpha.id))
    assert foreign.status_code == 404

    # A forged Beta tenant selector is a membership denial, not a way for an
    # Alpha agent to move the trust boundary across a tenant.
    forged_tenant = await client.post(
        endpoint,
        json=_action(beta_ticket, "claim"),
        headers=_headers(beta.id),
    )
    assert forged_tenant.status_code == 403
    unsafe = await client.post(endpoint, json=_action(non_synthetic, "claim"), headers=_headers(alpha.id))
    assert unsafe.status_code == 404
    invalid = await client.post(endpoint, json={**_action(alpha_ticket, "claim"), "organization_id": beta.id}, headers=_headers(alpha.id))
    assert invalid.status_code == 422
    assert (await _get_ticket(db, beta_ticket.id)).assigned_subject is None
    assert (await _get_ticket(db, non_synthetic.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_action_switch_fails_closed_by_default(db, client, org_scope, staging_gate, monkeypatch):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T4")
    monkeypatch.delenv("CXOPS_STAGING_TICKET_ACTIONS_ENABLED")
    assert not staging_request_allowed("POST", "/staging/ticket-action")
    response = await client.post("/staging/ticket-action", json=_action(ticket, "claim"), headers=_headers(alpha.id))
    assert response.status_code == 503
    assert (await _get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_only_exact_staging_actor_can_write(db, client, org_scope, staging_gate, monkeypatch):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T5")
    monkeypatch.setenv("CXOPS_STAGING_TICKET_AGENT_SUBJECT", str(uuid.uuid4()))
    response = await client.post("/staging/ticket-action", json=_action(ticket, "claim"), headers=_headers(alpha.id))
    assert response.status_code == 403
    assert (await _get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_concurrent_claims_one_winner(db, client, org_scope, staging_gate):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T6")
    async def attempt():
        return await client.post("/staging/ticket-action", json=_action(ticket, "claim"), headers=_headers(alpha.id))

    first, second = await asyncio.gather(attempt(), attempt())
    assert sorted((first.status_code, second.status_code)) == [200, 409]
    assert (await _get_ticket(db, ticket.id)).assigned_subject == ACTOR


@pytest.mark.asyncio
async def test_production_never_exposes_synthetic_staging_write_route(
    db, client, org_scope, staging_gate, monkeypatch,
):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T7")
    monkeypatch.setattr(main, "settings", SimpleNamespace(environment="production"))
    monkeypatch.setattr(
        action_route, "settings",
        SimpleNamespace(environment="production", auth_mode="jwks"),
    )
    denied = await client.post(
        "/staging/ticket-action",
        json=_action(ticket, "claim"),
        headers=_headers(alpha.id),
    )
    assert denied.status_code == 404
    assert (await _get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_staging_never_opens_general_ticket_mutation_endpoints(
    db, client, org_scope, staging_gate,
):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T8")
    denied = await client.patch(
        f"/tickets/{ticket.id}",
        json={"status": "solved"},
        headers=_headers(alpha.id),
    )
    assert denied.status_code == 503
    assert (await _get_ticket(db, ticket.id)).status == "open"


@pytest.mark.asyncio
async def test_production_blocks_anonymous_staging_route_before_auth_or_db(
    client, monkeypatch,
):
    # Even with the opt-in string present accidentally, production must not
    # expose route existence, require credentials or perform any DB lookup.
    monkeypatch.setenv("CXOPS_STAGING_TICKET_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(main, "settings", SimpleNamespace(environment="production"))
    for method, path in [
        ("POST", "/staging/ticket-action"),
        ("GET", "/staging/ticket-action"),
        ("HEAD", "/staging/ticket-action"),
    ]:
        result = await client.request(
            method, path,
            headers={"X-CXOps-Organization-ID": "1"},
        )
        assert result.status_code == 404
        assert result.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_disabled_staging_route_rejects_anonymous_before_auth_or_db(
    client, monkeypatch,
):
    monkeypatch.delenv("CXOPS_STAGING_TICKET_ACTIONS_ENABLED", raising=False)
    monkeypatch.setattr(main, "settings", SimpleNamespace(environment="staging"))
    response = await client.post(
        "/staging/ticket-action",
        json={"ticket_id": 1, "action": "resolve", "expected_version": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Staging operations are not enabled."}


@pytest.mark.asyncio
async def test_linked_non_synthetic_customer_cannot_be_changed_by_test_agent(
    db, client, org_scope, staging_gate,
):
    alpha, _ = await _setup(db, org_scope)
    suspicious_customer = Customer(
        organization_id=alpha.id,
        external_id="PHASE1X-ALPHA-CX",
        name="Not a synthetic customer",
        email="not-synthetic@example.org",
    )
    db.add(suspicious_customer)
    await db.commit()
    await db.refresh(suspicious_customer)
    ticket = await _make_ticket(
        db, alpha.id, external_id="PHASE1X-ALPHA-CUSTOMER-CHECK",
    )
    ticket.customer_id = suspicious_customer.id
    await db.commit()

    response = await client.post(
        "/staging/ticket-action",
        json=_action(ticket, "claim"),
        headers=_headers(alpha.id),
    )
    assert response.status_code == 404
    actual = await _get_ticket(db, ticket.id)
    assert actual.assigned_subject is None
    assert actual.status == "open"
    # The shared namespace teardown purges tickets first but does not own
    # standalone customer fixtures. Unlink and remove our injected customer
    # explicitly so the organization FK never outlives this test.
    actual.customer_id = None
    await db.commit()
    await db.delete(suspicious_customer)
    await db.commit()


@pytest.mark.asyncio
async def test_verified_synthetic_customer_link_remains_claimable(
    db, client, org_scope, staging_gate,
):
    alpha, _ = await _setup(db, org_scope)
    customer = Customer(
        organization_id=alpha.id,
        external_id="PHASE1X-ALPHA-C99",
        name="Synthetic Sample Customer",
        email="sample.alpha@example.test",
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    ticket = await _make_ticket(
        db, alpha.id, external_id="PHASE1X-ALPHA-T99",
    )
    ticket.customer_id = customer.id
    await db.commit()
    response = await client.post(
        "/staging/ticket-action",
        json=_action(ticket, "claim"),
        headers=_headers(alpha.id),
    )
    assert response.status_code == 200, response.text
    assert (await _get_ticket(db, ticket.id)).assigned_subject == ACTOR
    # Do not leave a fixture outside the module's targeted purge list.
    ticket.customer_id = None
    await db.commit()
    await db.delete(customer)
    await db.commit()
