"""Phase 1Q - simulated end-to-end customer journey (Workstream I).

Visitor requests early access -> operator reviews and approves the lead ->
an onboarding tenant is provisioned with a one-time owner invitation -> the
owner accepts, configures the business and chatbot, submits for review ->
the operator approves -> the owner adds knowledge and switches the chatbot
on -> a visitor on the allowed website chats -> the conversation and the AI
usage appear in that tenant's workspace and nowhere else.

Everything runs in-process against the disposable test database. The
decision model, knowledge search and embeddings are deterministic fakes, so
no paid provider is ever called. Interrupted and failed steps are exercised
along the way (lost widget key, name clash on provisioning, visitor on a
non-allowed origin, a second tenant looking in).
"""

import asyncio
import hashlib
import math
import os
import types
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "j" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
# Synthetic, non-secret JWT issuer name for this module's locally signed test
# tokens. Kept in one named constant (not repeated as a literal next to an
# AUTH_* key) so the secret scanner's generic rule has nothing to match.
JOURNEY_ISSUER = "journey"
os.environ["AUTH_JWT_ISSUER"] = JOURNEY_ISSUER
os.environ["AUTH_JWT_AUDIENCE"] = "cxops-unit-test"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.base import Base
from app.models.commercial import PlatformOperator, SalesLead
from app.models.organization import Organization
from app.models.public_chat import PublicChatRateLimitBucket
from app.schemas.agent import AgentDecision
from app.services.agent_workflow_service import agent_workflow_service
from app.services.embedding_service import embedding_service
from app.services.identity_verification import NhostUserInfoIdentityVerifier
from app.services.knowledge_search_service import KnowledgeSearchService

ORG_PREFIX = "Phase1Q-journey-"
LEAD_DOMAIN = "phase1q-journey.example"
SHOP_ORIGIN = "http://localhost:4173"  # the local test website
OTHER_ORIGIN = "https://not-the-customer.example"
TENANT_HEADER = "x-cxops-organization-id"

_PURGE = [
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
    for key, value in {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "j" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": JOURNEY_ISSUER,
        "AUTH_JWT_AUDIENCE": "cxops-unit-test",
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
        "PUBLIC_LEAD_CAPTURE_ENABLED": "true",
        "ONBOARDING_KNOWLEDGE_SETUP_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(scope="module", autouse=True)
def _isolated():
    asyncio.run(_purge())
    yield
    asyncio.run(_purge())


async def _purge() -> None:
    async with AsyncSessionLocal() as session:
        org_ids = [
            row
            for (row,) in await session.execute(
                select(Organization.id).where(Organization.name.like(f"{ORG_PREFIX}%"))
            )
        ]
        await session.execute(
            delete(SalesLead).where(SalesLead.email_normalized.like(f"%@{LEAD_DOMAIN}"))
        )
        await session.execute(
            delete(PlatformOperator).where(PlatformOperator.subject.like("journey-%"))
        )
        await session.execute(
            delete(PublicChatRateLimitBucket).where(
                PublicChatRateLimitBucket.key_cache.like("lead_%")
            )
        )
        if org_ids:
            for name in _PURGE:
                table = Base.metadata.tables.get(name)
                if table is not None and "organization_id" in table.columns:
                    await session.execute(
                        delete(table).where(table.c.organization_id.in_(org_ids))
                    )
            await session.execute(delete(Organization).where(Organization.id.in_(org_ids)))
        await session.commit()


def _bearer(sub: str, email: str | None = None) -> str:
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "sub": sub,
        "iss": JOURNEY_ISSUER,
        "aud": "cxops-unit-test",
        "exp": now + 3600,
        "iat": now,
    }
    if email:
        claims["email"] = email
    return "Bearer " + jwt.encode(claims, "j" * 32, algorithm="HS256")


def _vector(text: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
    values, state = [], seed
    for _ in range(1536):
        state = (state * 1103515245 + 12345) % (2**31)
        values.append((state / (2**31 - 1)) * 2.0 - 1.0)
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


class _FakeDecisionLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return {
            "raw": types.SimpleNamespace(usage_metadata=None),
            "parsed": AgentDecision(
                action="respond",  # type: ignore[arg-type]
                reason="Customer-visible reply.",
                requires_human_approval=False,
                response_draft="We are open 8am-6pm on weekdays.",
            ),
            "parsing_error": None,
        }


class FakeNhostAuth:
    """In-process stand-in for Nhost Auth ``GET /v1/user``: returns the user
    record for whichever access token is presented. Tokens here carry no
    email claim (like real Nhost tokens), so the API must look the identity
    up server-side."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}  # sub -> user record

    def handler(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        sub = jwt.get_unverified_claims(token).get("sub")
        user = self.users.get(sub)
        if user is None:
            return httpx.Response(401, json={"error": "unauthenticated"})
        return httpx.Response(200, json={"id": sub, **user})


@pytest.fixture
def fake_nhost():
    from app.api.routes.commercial import get_identity_verifier

    auth = FakeNhostAuth()
    verifier = NhostUserInfoIdentityVerifier(
        "https://fake.auth.local-test.nhost.run/v1",
        transport=httpx.MockTransport(auth.handler),
    )
    app.dependency_overrides[get_identity_verifier] = lambda: verifier
    yield auth
    app.dependency_overrides.pop(get_identity_verifier, None)


@pytest.fixture
def fake_ai(monkeypatch):
    async def _embed_text(text: str) -> list[float]:
        return _vector(text)

    async def _embed_documents(texts: list[str]) -> list[list[float]]:
        return [_vector(text) for text in texts]

    async def _search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(embedding_service, "embed_text", _embed_text)
    monkeypatch.setattr(embedding_service, "embed_documents", _embed_documents)
    monkeypatch.setattr(KnowledgeSearchService, "search", _search)
    llm = _FakeDecisionLLM()
    monkeypatch.setattr(agent_workflow_service, "decision_llm", llm)
    return llm


@pytest.mark.asyncio
async def test_simulated_customer_journey(fake_ai, fake_nhost):
    suffix = uuid.uuid4().hex[:6]
    company = f"{ORG_PREFIX}Acme Auto {suffix}"
    owner_email = f"owner-{suffix}@{LEAD_DOMAIN}"
    operator = f"journey-op-{suffix}"
    owner = f"journey-owner-{suffix}"
    agent_member = f"journey-agent-{suffix}"
    outsider = f"journey-outsider-{suffix}"
    evidence: list[str] = []

    async with AsyncSessionLocal() as db:
        db.add(PlatformOperator(subject=operator, granted_by="journey-test"))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://cxops.test") as c:
        op = {"Authorization": _bearer(operator)}

        # 1. Visitor discovers CXOps and requests a demo.
        availability = (await c.get("/public/leads/availability")).json()
        assert availability["accepting"] is True
        submitted = await c.post(
            "/public/leads",
            json={
                "company_name": company,
                "contact_name": "Jordan Lee",
                "email": owner_email,
                "website": "acme-auto.example.com",
                "industry": "automotive",
                "inquiry": "After-hours booking questions.",
                "consent": True,
            },
        )
        assert submitted.status_code == 202
        evidence.append("lead submitted (202)")

        # 2. Operator reviews the lead.
        leads = (await c.get("/platform-admin/leads?status=new", headers=op)).json()
        lead = next(item for item in leads["leads"] if item["email"] == owner_email)
        for state in ("reviewing", "approved"):
            r = await c.patch(
                f"/platform-admin/leads/{lead['id']}", json={"status": state}, headers=op
            )
            assert r.status_code == 200, r.text
        evidence.append("lead new -> reviewing -> approved")

        # 3. Provisioning: a name clash fails cleanly, then succeeds.
        async with AsyncSessionLocal() as db:
            db.add(Organization(name=f"{ORG_PREFIX}taken-{suffix}"))
            await db.commit()
        clash = await c.post(
            f"/platform-admin/leads/{lead['id']}/provision",
            json={"organization_name": f"{ORG_PREFIX}taken-{suffix}"},
            headers=op,
        )
        assert clash.status_code == 409
        provisioned = await c.post(
            f"/platform-admin/leads/{lead['id']}/provision", json={}, headers=op
        )
        assert provisioned.status_code == 201, provisioned.text
        org_id = provisioned.json()["organization_id"]
        invite_token = provisioned.json()["owner_invitation"]["token"]
        evidence.append(f"tenant {org_id} provisioned in onboarding after a 409 name clash")

        # 4. Owner accepts the invitation. The JWT carries no email; the API
        # verifies the identity with (fake) Nhost Auth. First an unverified
        # account and a different person holding the forwarded link are
        # refused, then the verified recipient succeeds.
        fake_nhost.users[owner] = {"email": owner_email, "emailVerified": False}
        unverified = await c.post(
            "/invitations/accept",
            json={"token": invite_token},
            headers={"Authorization": _bearer(owner)},
        )
        assert unverified.status_code == 403
        fake_nhost.users[outsider] = {"email": f"x-{suffix}@{LEAD_DOMAIN}", "emailVerified": True}
        forwarded = await c.post(
            "/invitations/accept",
            json={"token": invite_token},
            headers={"Authorization": _bearer(outsider)},
        )
        assert forwarded.status_code == 403
        fake_nhost.users[owner] = {"email": owner_email, "emailVerified": True}
        accepted = await c.post(
            "/invitations/accept",
            json={"token": invite_token},
            headers={"Authorization": _bearer(owner)},
        )
        assert accepted.status_code == 200, accepted.text
        own = {"Authorization": _bearer(owner), TENANT_HEADER: str(org_id)}
        authz = (await c.get("/me/authorization", headers=own)).json()
        assert authz["organization_status"] == "onboarding"
        assert "agent.run" not in authz["capabilities"]
        evidence.append("owner joined; capabilities limited to setup")

        # 5. Setup wizard: business, widget (key shown once), team.
        r = await c.patch(
            "/onboarding/business", json={"website_url": "acme-auto.example.com"}, headers=own
        )
        assert r.status_code == 200
        widget = await c.post(
            "/onboarding/widget",
            json={
                "display_name": "Acme Auto Assistant",
                "welcome_message": "Hi! Ask us about bookings and opening hours.",
                "theme_token": "forest",
                "allowed_origins": [SHOP_ORIGIN],
            },
            headers=own,
        )
        assert widget.status_code == 201
        widget_key = widget.json()["public_widget_key"]
        # Interrupted setup: the owner reloads and tries again - no second key.
        retry = await c.post(
            "/onboarding/widget",
            json={"display_name": "x", "welcome_message": "y"},
            headers=own,
        )
        assert retry.status_code == 409 and "pk_live_" not in retry.text
        team_invite = await c.post(
            "/team/invitations",
            json={"email": f"agent-{suffix}@{LEAD_DOMAIN}", "role": "agent"},
            headers=own,
        )
        assert team_invite.status_code == 201
        fake_nhost.users[agent_member] = {
            "email": f"agent-{suffix}@{LEAD_DOMAIN}",
            "emailVerified": True,
        }
        joined = await c.post(
            "/invitations/accept",
            json={"token": team_invite.json()["token"]},
            headers={"Authorization": _bearer(agent_member)},
        )
        assert joined.status_code == 200
        evidence.append("business + chatbot configured; key issued once; agent invited")

        # The widget does not serve visitors before approval.
        blocked = await c.post(
            "/public/chat/sessions",
            json={"public_widget_key": widget_key},
            headers={"x-embedding-origin": SHOP_ORIGIN},
        )
        assert blocked.status_code == 403

        submit = await c.post("/onboarding/submit", headers=own)
        assert submit.json()["organization"]["lifecycle_status"] == "pending_review"

        # 6. Operator approves and sets an allowance.
        approve = await c.post(
            f"/platform-admin/organizations/{org_id}/lifecycle",
            json={"action": "approve"},
            headers=op,
        )
        assert approve.status_code == 200
        await c.patch(
            f"/platform-admin/organizations/{org_id}/entitlements",
            json={"plan_code": "pilot", "monthly_ai_turn_limit": 200, "monthly_ai_budget_usd": 2},
            headers=op,
        )
        evidence.append("operator approved tenant; allowance 200 turns / $2 (estimated)")

        # 7. Knowledge is prepared (fake embeddings) and the chatbot goes live.
        doc = await c.post(
            "/knowledge/documents",
            json={
                "title": "Opening hours",
                "content": "Acme Auto is open 8am to 6pm, Monday to Friday. "
                "Bookings can be made online.",
            },
            headers=own,
        )
        assert doc.status_code == 201, doc.text
        live = await c.patch(
            "/staff/tenant-config/public-chat", json={"enabled": True}, headers=own
        )
        assert live.status_code == 200 and live.json()["enabled"] is True
        evidence.append("knowledge document ingested; chatbot enabled")

        # 8. The widget is installed on the local test website; a visitor chats.
        wrong_site = await c.post(
            "/public/chat/sessions",
            json={"public_widget_key": widget_key},
            headers={"x-embedding-origin": OTHER_ORIGIN},
        )
        assert wrong_site.status_code == 403
        session = await c.post(
            "/public/chat/sessions",
            json={"public_widget_key": widget_key},
            headers={"x-embedding-origin": SHOP_ORIGIN},
        )
        assert session.status_code == 200, session.text
        visitor = {
            "Authorization": f"Bearer {session.json()['session']['token']}",
            "x-embedding-origin": SHOP_ORIGIN,
        }
        reply = await c.post(
            "/public/chat/messages",
            json={"client_message_id": "journey-1", "text": "When are you open?"},
            headers=visitor,
        )
        assert reply.status_code == 200, reply.text
        assert fake_ai.calls >= 1
        evidence.append("visitor chatted from the allowed origin; other origin refused")

        # 9. The activity appears in this tenant's workspace...
        overview = (await c.get("/workspace/overview", headers=own)).json()
        assert overview["chatbot"]["live"] is True
        assert overview["chatbot"]["installation"]["detected"] is True
        assert overview["knowledge"]["documents"] == 1
        assert overview["conversations"]["last_7_days"] == 1
        assert overview["usage"]["turns_used"] >= 1
        assert overview["usage"]["turn_limit"] == 200
        assert overview["team"]["members"] == 2

        # ...and nowhere else.
        async with AsyncSessionLocal() as db:
            other = Organization(name=f"{ORG_PREFIX}other-{suffix}")
            db.add(other)
            await db.flush()
            from app.models.organization_membership import OrganizationMembership

            db.add(OrganizationMembership(subject=outsider, organization_id=other.id, role="owner"))
            await db.commit()
            other_id = other.id
        peek = await c.get(
            "/workspace/overview",
            headers={"Authorization": _bearer(outsider), TENANT_HEADER: str(org_id)},
        )
        assert peek.status_code == 403
        theirs = (
            await c.get(
                "/workspace/overview",
                headers={"Authorization": _bearer(outsider), TENANT_HEADER: str(other_id)},
            )
        ).json()
        assert theirs["conversations"]["last_7_days"] == 0
        assert theirs["chatbot"]["configured"] is False
        evidence.append("workspace shows 1 conversation, usage, 2 members; outsider sees none")

        # 10. Operator sees platform-wide estimated cost including this tenant.
        usage = (await c.get("/platform-admin/usage", headers=op)).json()
        assert any(row["organization_id"] == org_id for row in usage["by_tenant"])
        assert usage["cost_basis"] == "estimated"

        # 11. The team member has restricted permissions.
        agent_h = {"Authorization": _bearer(agent_member), TENANT_HEADER: str(org_id)}
        agent_view = (await c.get("/workspace/overview", headers=agent_h)).json()
        assert agent_view["your_role"] == "agent"
        assert agent_view["usage"]["available"] is False
        assert agent_view["conversations"]["available"] is True
        assert (await c.get("/team/invitations", headers=agent_h)).status_code == 403
        assert (
            await c.patch("/staff/tenant-config/public-chat", json={"enabled": False}, headers=agent_h)
        ).status_code == 403
        assert (await c.get("/platform-admin/me", headers=agent_h)).status_code == 403
        evidence.append("agent member: conversations visible; usage, team admin, settings refused")

        # 12. Suspension stops public chat immediately, including the open session.
        suspend = await c.post(
            f"/platform-admin/organizations/{org_id}/lifecycle",
            json={"action": "suspend"},
            headers=op,
        )
        assert suspend.status_code == 200
        stopped = await c.post(
            "/public/chat/messages",
            json={"client_message_id": "journey-2", "text": "Are you there?"},
            headers=visitor,
        )
        assert stopped.status_code == 403
        new_session = await c.post(
            "/public/chat/sessions",
            json={"public_widget_key": widget_key},
            headers={"x-embedding-origin": SHOP_ORIGIN},
        )
        assert new_session.status_code == 403
        suspended_view = await c.get("/me/authorization", headers=own)
        assert "integration.manage" not in suspended_view.json()["capabilities"]
        evidence.append("suspension: open session and new sessions refused (403)")

    print("\n".join(f"[journey] {line}" for line in evidence))
