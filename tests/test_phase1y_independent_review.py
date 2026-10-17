"""Phase 1Y independent security review: adversarial additions (PR #38).

Same disposable-PostgreSQL / injected-principal seam as the original suite
(real durable membership and capability checks); synthetic data only.
"""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update

from app import main
from app.api.deps import get_current_principal_dep
from app.api.routes import staging_ticket_action as action_route
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from tests import test_phase1y_synthetic_ticket_actions as base

_configure_auth = base._configure_auth
_p1p7r_namespace_isolated = base._p1p7r_namespace_isolated
client = base.client
db = base.db
org_scope = base.org_scope
staging_gate = base.staging_gate

ENDPOINT = "/staging/ticket-action"


async def _alpha_ticket(db, org_scope, external_id="PHASE1X-ALPHA-T-REV"):
    alpha, beta = await base._setup(db, org_scope)
    ticket = await base._make_ticket(db, alpha.id, external_id=external_id)
    return alpha, beta, ticket


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"ticket_id": 1, "action": "claim", "organization_id": 999},
        {"ticket_id": 1, "action": "claim", "assigned_subject": "someone-else"},
        {"ticket_id": 1, "action": "claim", "status": "solved"},
        {"ticket_id": True, "action": "claim"},
        {"ticket_id": "1", "action": "claim"},
        {"ticket_id": 1.5, "action": "claim"},
        {"ticket_id": 0, "action": "claim"},
        {"ticket_id": -4, "action": "claim"},
        {"ticket_id": 2**40, "action": "claim"},
        {"ticket_id": 1, "action": "reopen"},
        {"ticket_id": 1, "action": "CLAIM"},
        {"ticket_id": 1, "action": ["claim"]},
        {"ticket_id": 1},
        {"action": "claim"},
        {},
    ],
)
async def test_request_body_is_strict_and_never_touches_the_ticket(
    body, db, client, org_scope, staging_gate
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    payload = dict(body)
    if payload.get("ticket_id") == 1 and payload["ticket_id"] is not True:
        payload["ticket_id"] = ticket.id
    response = await client.post(
        ENDPOINT, json=payload, headers=base._headers(alpha.id)
    )
    assert response.status_code == 422, response.text
    fresh = await base._get_ticket(db, ticket.id)
    assert fresh.assigned_subject is None and fresh.status == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/staging/ticket-action/",
        "//staging/ticket-action",
        "/staging/ticket-action/.",
        "/staging/ticket-action%2F",
        "/staging/",
        "/staging/anything",
    ],
)
async def test_production_does_not_reveal_the_hidden_route_through_path_variants(
    path, client, monkeypatch
):
    monkeypatch.setattr(main, "settings", SimpleNamespace(environment="production"))
    for method in ("POST", "GET"):
        response = await client.request(
            method,
            path,
            headers={"X-CXOps-Organization-ID": "1"},
            follow_redirects=False,
        )
        # a 307/308 here would disclose that the hidden route exists
        assert response.status_code in (404, 405), (method, path, response.status_code)
        assert "location" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["solved", "closed", "on_hold", "deleted", "archived", ""]
)
async def test_only_active_tickets_can_change(
    state, db, client, org_scope, staging_gate
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    await db.execute(update(Ticket).where(Ticket.id == ticket.id).values(status=state))
    await db.commit()
    for action in ("claim", "release", "resolve"):
        response = await client.post(
            ENDPOINT,
            json=base._action(await base._get_ticket(db, ticket.id), action),
            headers=base._headers(alpha.id),
        )
        assert response.status_code == 409
    assert (await base._get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_a_resolved_ticket_is_terminal_for_this_surface(
    db, client, org_scope, staging_gate
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    headers = base._headers(alpha.id)
    for action in ("claim", "resolve"):
        current = await base._get_ticket(db, ticket.id)
        assert (
            await client.post(
                ENDPOINT, json=base._action(current, action), headers=headers
            )
        ).status_code == 200
    resolved_at = (await base._get_ticket(db, ticket.id)).resolved_at
    for action in ("claim", "release", "resolve"):
        again = await client.post(
            ENDPOINT,
            json=base._action(await base._get_ticket(db, ticket.id), action),
            headers=headers,
        )
        assert again.status_code == 409
    assert (await base._get_ticket(db, ticket.id)).resolved_at == resolved_at


@pytest.mark.asyncio
async def test_actions_cause_no_side_effects_and_make_no_network_connection(
    db, client, org_scope, staging_gate, monkeypatch
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    tables = (
        IntegrationJob,
        AIRequestLog,
        AgentRun,
        TicketEvent,
        Conversation,
        ConversationMessage,
    )

    async def counts():
        return [
            (await db.execute(select(func.count()).select_from(model))).scalar_one()
            for model in tables
        ]

    before = await counts()
    real_connect = socket.socket.connect
    attempted: list[object] = []

    def refuse_non_database(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempted.append(address)
            raise OSError("network blocked by test")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", refuse_non_database)
    headers = base._headers(alpha.id)
    for action in ("claim", "release", "claim", "resolve"):
        current = await base._get_ticket(db, ticket.id)
        response = await client.post(
            ENDPOINT, json=base._action(current, action), headers=headers
        )
        assert response.status_code == 200, response.text
        assert set(response.json()) == {
            "id",
            "external_id",
            "status",
            "assigned_to_you",
            "resolved_at",
            "version",
        }
    assert attempted == []
    assert await counts() == before


@pytest.mark.asyncio
async def test_many_concurrent_claims_have_exactly_one_winner(
    db, client, org_scope, staging_gate
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    results = await asyncio.gather(
        *[
            client.post(
                ENDPOINT,
                json=base._action(ticket, "claim"),
                headers=base._headers(alpha.id),
            )
            for _ in range(12)
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200] + [409] * 11
    assert (await base._get_ticket(db, ticket.id)).assigned_subject == base.ACTOR


@pytest.mark.asyncio
async def test_concurrent_release_and_resolve_leave_a_consistent_state(
    db, client, org_scope, staging_gate
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    headers = base._headers(alpha.id)
    claimed = await client.post(
        ENDPOINT, json=base._action(ticket, "claim"), headers=headers
    )
    claimed_version = claimed.json()["version"]
    rel, res = await asyncio.gather(
        client.post(
            ENDPOINT,
            json={
                **base._action(ticket, "release"),
                "expected_version": claimed_version,
            },
            headers=headers,
        ),
        client.post(
            ENDPOINT,
            json={
                **base._action(ticket, "resolve"),
                "expected_version": claimed_version,
            },
            headers=headers,
        ),
    )
    assert sorted([rel.status_code, res.status_code]) == [200, 409]
    final = await base._get_ticket(db, ticket.id)
    if res.status_code == 200:
        assert final.status == "solved" and final.resolved_at is not None
    else:
        assert final.status == "open" and final.assigned_subject is None
        assert final.resolved_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed_action", ["release", "resolve"])
async def test_delayed_command_from_an_earlier_claim_generation_is_rejected(
    delayed_action, db, client, org_scope, staging_gate
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    headers = base._headers(alpha.id)
    first_claim = await client.post(
        ENDPOINT, json=base._action(ticket, "claim"), headers=headers
    )
    old_claim_version = first_claim.json()["version"]
    released = await client.post(
        ENDPOINT,
        json={**base._action(ticket, "release"), "expected_version": old_claim_version},
        headers=headers,
    )
    second_claim = await client.post(
        ENDPOINT,
        json={
            **base._action(ticket, "claim"),
            "expected_version": released.json()["version"],
        },
        headers=headers,
    )
    replay = await client.post(
        ENDPOINT,
        json={
            **base._action(ticket, delayed_action),
            "expected_version": old_claim_version,
        },
        headers=headers,
    )
    assert replay.status_code == 409
    final = await base._get_ticket(db, ticket.id)
    assert final.assigned_subject == base.ACTOR
    assert final.status == "open"
    assert (
        final.updated_at.isoformat().replace("+00:00", "Z")
        == second_claim.json()["version"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["delete", "demote", "suspend"])
async def test_authorization_revocation_is_fenced_before_ticket_mutation(
    revocation, db, client, org_scope, staging_gate
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    async with AsyncSessionLocal() as operator:
        locked_org = (
            await operator.execute(
                select(Organization)
                .where(Organization.id == alpha.id)
                .with_for_update()
            )
        ).scalar_one()
        membership = (
            await operator.execute(
                select(OrganizationMembership)
                .where(
                    OrganizationMembership.organization_id == alpha.id,
                    OrganizationMembership.subject == base.ACTOR,
                )
                .with_for_update()
            )
        ).scalar_one()
        request = asyncio.create_task(
            client.post(
                ENDPOINT,
                json=base._action(ticket, "claim"),
                headers=base._headers(alpha.id),
            )
        )
        await asyncio.sleep(0.05)
        if revocation == "delete":
            await operator.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.id == membership.id
                )
            )
        elif revocation == "demote":
            membership.role = "viewer"
        else:
            locked_org.lifecycle_status = "suspended"
        await operator.commit()
    response = await asyncio.wait_for(request, timeout=3)
    assert response.status_code == 403
    final = await base._get_ticket(db, ticket.id)
    assert final.assigned_subject is None
    assert final.status == "open"


@pytest.mark.asyncio
async def test_a_second_alpha_agent_cannot_use_the_surface(
    db, client, org_scope, staging_gate
):
    """Only the exact configured subject may write, even with a real AGENT role."""
    from app.core.rbac import OrganizationRole

    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    other = "00000000-0000-4000-8000-000000001003"
    await base.base._add_membership(db, other, alpha.id, role=OrganizationRole.AGENT)
    staging_gate(other)
    response = await client.post(
        ENDPOINT,
        json=base._action(ticket, "claim"),
        headers=base._headers(alpha.id),
    )
    assert response.status_code == 403
    assert (await base._get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_a_non_neon_principal_with_the_right_subject_is_refused(
    db, client, org_scope, staging_gate
):
    from app.core.principal import AuthenticatedPrincipal

    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    app.dependency_overrides[get_current_principal_dep] = lambda: (
        AuthenticatedPrincipal(
            subject=base.ACTOR,
            email="x@example.test",
            issuer="https://other.example",
            auth_method="hs256",
        )
    )
    response = await client.post(
        ENDPOINT,
        json=base._action(ticket, "claim"),
        headers=base._headers(alpha.id),
    )
    assert response.status_code == 403
    assert (await base._get_ticket(db, ticket.id)).assigned_subject is None


@pytest.mark.asyncio
async def test_an_enabled_route_still_refuses_when_the_subject_is_not_configured(
    db, client, org_scope, staging_gate, monkeypatch
):
    alpha, _beta, ticket = await _alpha_ticket(db, org_scope)
    monkeypatch.delenv("CXOPS_STAGING_TICKET_AGENT_SUBJECT", raising=False)
    response = await client.post(
        ENDPOINT,
        json=base._action(ticket, "claim"),
        headers=base._headers(alpha.id),
    )
    assert response.status_code == 403
    assert action_route is not None
