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
        SimpleNamespace(environment="staging", auth_mode="neon_staging"),
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
        endpoint, json={"ticket_id": ticket.id, "action": "claim"},
        headers=_headers(alpha.id),
    )
    assert result.status_code == 200, result.text
    assert result.json()["assigned_to_you"] is True
    assert (await _get_ticket(db, ticket.id)).assigned_subject == ACTOR

    repeat = await client.post(endpoint, json={"ticket_id": ticket.id, "action": "claim"}, headers=_headers(alpha.id))
    assert repeat.status_code == 409

    release = await client.post(endpoint, json={"ticket_id": ticket.id, "action": "release"}, headers=_headers(alpha.id))
    assert release.status_code == 200
    assert release.json()["assigned_to_you"] is False

    premature = await client.post(endpoint, json={"ticket_id": ticket.id, "action": "resolve"}, headers=_headers(alpha.id))
    assert premature.status_code == 409

    reassign = await client.post(endpoint, json={"ticket_id": ticket.id, "action": "claim"}, headers=_headers(alpha.id))
    assert reassign.status_code == 200
    solved = await client.post(endpoint, json={"ticket_id": ticket.id, "action": "resolve"}, headers=_headers(alpha.id))
    assert solved.status_code == 200
    assert solved.json()["status"] == "solved"
    assert solved.json()["resolved_at"]
    assert (await _get_ticket(db, ticket.id)).resolved_at is not None

    stale = await client.post(endpoint, json={"ticket_id": ticket.id, "action": "resolve"}, headers=_headers(alpha.id))
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
    viewer = await client.post(endpoint, json={"ticket_id": alpha_ticket.id, "action": "claim"}, headers=_headers(alpha.id))
    assert viewer.status_code == 403
    staging_gate(ACTOR)

    foreign = await client.post(endpoint, json={"ticket_id": beta_ticket.id, "action": "claim"}, headers=_headers(alpha.id))
    assert foreign.status_code == 404
    unsafe = await client.post(endpoint, json={"ticket_id": non_synthetic.id, "action": "claim"}, headers=_headers(alpha.id))
    assert unsafe.status_code == 404
    invalid = await client.post(endpoint, json={"ticket_id": alpha_ticket.id, "action": "claim", "organization_id": beta.id}, headers=_headers(alpha.id))
    assert invalid.status_code == 422
    assert (await _get_ticket(db, beta_ticket.id)).assigned_subject is None
    assert (await _get_ticket(db, non_synthetic.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_action_switch_fails_closed_by_default(db, client, org_scope, staging_gate, monkeypatch):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T4")
    monkeypatch.delenv("CXOPS_STAGING_TICKET_ACTIONS_ENABLED")
    assert not staging_request_allowed("POST", "/staging/ticket-action")
    response = await client.post("/staging/ticket-action", json={"ticket_id": ticket.id, "action": "claim"}, headers=_headers(alpha.id))
    assert response.status_code == 503
    assert (await _get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_only_exact_staging_actor_can_write(db, client, org_scope, staging_gate, monkeypatch):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T5")
    monkeypatch.setenv("CXOPS_STAGING_TICKET_AGENT_SUBJECT", str(uuid.uuid4()))
    response = await client.post("/staging/ticket-action", json={"ticket_id": ticket.id, "action": "claim"}, headers=_headers(alpha.id))
    assert response.status_code == 403
    assert (await _get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_concurrent_claims_one_winner(db, client, org_scope, staging_gate):
    alpha, _ = await _setup(db, org_scope)
    ticket = await _make_ticket(db, alpha.id, external_id="PHASE1X-ALPHA-T6")
    async def attempt():
        return await client.post("/staging/ticket-action", json={"ticket_id": ticket.id, "action": "claim"}, headers=_headers(alpha.id))

    first, second = await asyncio.gather(attempt(), attempt())
    assert sorted((first.status_code, second.status_code)) == [200, 409]
    assert (await _get_ticket(db, ticket.id)).assigned_subject == ACTOR
