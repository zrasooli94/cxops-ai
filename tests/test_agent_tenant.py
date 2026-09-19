"""Agent runtime / approval / execution tenant-isolation security tests (Phase 1C.3C).

Two synthetic organizations (Org A → user-alpha, Org B → user-beta) with no
shared membership prove that every tenant-facing agent surface is bounded by
the resolved organization:

- analyze / start-run ownership is copied from the tenant's persisted ticket
- run list / read / approve / reject / execute are tenant-scoped by run_id
- event streams are tenant-scoped (non-enumerating 404 for foreign runs)
- the database rejects a run whose organization drifted from its ticket
- legacy NULL-org runs are inert for every tenant path
- agent RAG retrieval and Zendesk execution use the run/ticket organization

The decision LLM is monkeypatched to a deterministic fake so the suite never
touches OpenAI. Zendesk network calls are intercepted at the client boundary
so execution tests assert which organization credential would have been used.

Tests A–R + the cross-tenant side-effect regression map 1:1 onto the Phase
1C.3C spec list. ``test_run_ticket_org_mismatch_rejected`` is the database
tenant-consistency gate for AgentRun/Ticket ownership.
"""

import asyncio
import inspect
import os
import pathlib
import time
import types
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-agent-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-agent-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.api.routes import agent as agent_route_module
from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal, engine
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket
from app.repositories.agent_run_repository import AgentRunRepository
from app.schemas.agent import AgentDecision
from app.services import (
    agent_approval_service as agent_approval_service_module,
)
from app.services import (
    agent_execution_service as agent_execution_service_module,
)
from app.services import (
    agent_workflow_service as agent_workflow_service_module,
)
from app.services import (
    integration_job_service as integration_job_service_module,
)
from app.services.agent_execution_service import agent_execution_service
from app.services.agent_workflow_service import agent_workflow_service
from app.services.knowledge_search_service import KnowledgeSearchService
from app.services.tool_authorization_service import ToolAuthorizationService
from app.services.zendesk_sync_service import ZendeskSyncService

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-agent-issuer"
TEST_AUDIENCE = "test-agent-audience"

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


def _auth_headers(sub: str, *, tenant_id: int | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_build_token(sub=sub)}"}
    if tenant_id is not None:
        headers[X_TENANT] = str(tenant_id)
    return headers


def _authorized_internal_note_run(
    run_id: str,
    *,
    organization_id: int,
    ticket_id: int,
    reason: str,
    subject: str = "cxops-test",
) -> dict:
    """Return Phase 1D.3-complete metadata for a low-risk executable run.

    The plan carries the policy ``required_capability`` and the digest binds
    the persisted ``tool_policy_version``, so the execution preflight (policy
    version, intent digest, per-tool capability) can pass fail-closed checks.
    """
    plan = [
        {
            "tool": "zendesk.add_internal_note",
            "arguments": {"reason": reason},
            "requires_approval": False,
            "authorized": True,
            "risk_level": "low",
            "required_capability": "ticket.write",
        }
    ]
    return {
        "plan": plan,
        "tool_policy_version": ToolAuthorizationService.TOOL_POLICY_VERSION,
        "authorization_source": "policy_auto",
        "authorized_by_subject": subject,
        "authorization_digest": ToolAuthorizationService.compute_run_digest(
            run_id=run_id,
            organization_id=organization_id,
            ticket_id=ticket_id,
            policy_version=ToolAuthorizationService.TOOL_POLICY_VERSION,
            tool_plan=plan,
        ),
    }


class _FakeDecisionLLM:
    """Deterministic LangGraph decision model (never reaches OpenAI)."""

    def __init__(self, decision: AgentDecision) -> None:
        self._decision = decision

    async def ainvoke(self, _messages: list) -> dict:
        raw = types.SimpleNamespace(usage_metadata=None)
        return {
            "raw": raw,
            "parsed": self._decision,
            "parsing_error": None,
        }


def _respond_decision(*, reason: str = "Draft a safe customer reply.") -> AgentDecision:
    return AgentDecision(
        action="respond",
        reason=reason,
        requires_human_approval=True,
        response_draft="This is an owned response draft that a foreign tenant "
        "must never see.",
    )


@pytest.fixture
def fake_llm(monkeypatch):
    """Install a deterministic decision model for the workflow service."""

    def _install(decision: AgentDecision) -> None:
        monkeypatch.setattr(
            agent_workflow_service,
            "decision_llm",
            _FakeDecisionLLM(decision),
        )

    return _install


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def two_orgs(db):
    """Org A (user-alpha), Org B (user-beta) + ticket/run/integration helpers.

    Teardown wipes runs/events, tickets, integration jobs, memberships,
    logs, and organizations so the suite is repeatable against a shared DB.
    """
    org_ids: list[int] = []
    log_ids: list[str] = []
    captured_run_ids: list[str] = []
    captured_ticket_ids: list[int] = []

    async def make_org(name: str, *, subject: str | None = None) -> Organization:
        org = Organization(name=name)
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        if subject is not None:
            db.add(
                OrganizationMembership(
                    subject=subject,
                    organization_id=org.id,
                    role=OrganizationRole.OWNER,
                )
            )
        await db.commit()
        return org

    org_a = await make_org(f"org-a-{uuid.uuid4().hex[:8]}", subject=USER_ALPHA)
    org_b = await make_org(f"org-b-{uuid.uuid4().hex[:8]}", subject=USER_BETA)

    async def make_ticket(
        organization_id: int | None,
        *,
        subject: str,
        description: str = "Customer support case.",
        external_id: str | None = None,
        source: str | None = None,
    ) -> Ticket:
        ticket = Ticket(
            organization_id=organization_id,
            subject=subject,
            description=description,
            external_id=external_id,
            source=source,
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)
        captured_ticket_ids.append(ticket.id)
        return ticket

    async def make_run(
        organization_id: int | None,
        ticket_id: int,
        *,
        status: str = "pending_approval",
        action: str = "respond",
        reason: str = "Reason for a run.",
        response_draft: str | None = None,
        reviewer_note: str | None = None,
        plan: list[dict] | None = None,
        run_id: str | None = None,
        tool_policy_version: int | None = None,
        authorization_source: str | None = None,
        authorized_by_subject: str | None = None,
        authorization_digest: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            run_id=run_id or uuid.uuid4().hex,
            ticket_id=ticket_id,
            organization_id=organization_id,
            action=action,
            reason=reason,
            response_draft=response_draft,
            reviewer_note=reviewer_note,
            status=status,
            sources=[],
            workflow_path=["load_ticket"],
            tool_plan=plan or [],
            tool_policy_version=tool_policy_version,
            authorization_source=authorization_source,
            authorized_by_subject=authorized_by_subject,
            authorization_digest=authorization_digest,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        captured_run_ids.append(run.run_id)
        return run

    async def add_membership(subject: str, organization_id: int) -> None:
        db.add(
            OrganizationMembership(
                subject=subject,
                organization_id=organization_id,
                role=OrganizationRole.OWNER,
            )
        )
        await db.commit()

    async def track_log(request_id: str) -> None:
        log_ids.append(request_id)

    yield {
        "org_a": org_a,
        "org_b": org_b,
        "make_ticket": make_ticket,
        "make_run": make_run,
        "add_membership": add_membership,
        "track_log": track_log,
    }

    if org_ids:
        await db.execute(
            delete(AgentRun).where(AgentRun.organization_id.in_(org_ids))
        )
        await db.execute(
            delete(IntegrationJob).where(IntegrationJob.organization_id.in_(org_ids))
        )
    if captured_run_ids:
        await db.execute(
            delete(AgentRun).where(AgentRun.run_id.in_(captured_run_ids))
        )
    if captured_ticket_ids:
        await db.execute(delete(Ticket).where(Ticket.id.in_(captured_ticket_ids)))
    if org_ids:
        # Telemetry/log rows reference organizations; delete before org teardown.
        await db.execute(delete(AIRequestLog).where(AIRequestLog.organization_id.in_(org_ids)))
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


# ===================================================================
# A. list runs only within the tenant
# ===================================================================


@pytest.mark.asyncio
async def test_a_list_returns_only_owned_runs(db, client, two_orgs):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_a = await two_orgs["make_ticket"](org_a.id, subject="Alpha ticket")
    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")

    run_a = await two_orgs["make_run"](
        org_a.id, ticket_a.id, action="respond", reason="Alpha reason."
    )
    await two_orgs["make_run"](
        org_b.id, ticket_b.id, action="escalate", reason="Beta reason."
    )

    response = await client.get(
        "/agent/runs",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 200
    run_ids = [item["run_id"] for item in response.json()]
    assert run_a.run_id in run_ids
    assert all(
        item.get("organization_id") == org_a.id for item in response.json()
    )
    assert "Beta reason." not in response.text


# ===================================================================
# B. foreign run id never resolves (non-enumerating 404)
# ===================================================================


@pytest.mark.asyncio
async def test_b_foreign_run_never_resolves_by_id(db, client, two_orgs):
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        action="respond",
        reason="Secret beta reasoning.",
        response_draft="Secret beta draft.",
    )

    headers = _auth_headers(USER_ALPHA)

    for endpoint, method in (
        (f"/agent/runs/{run_b.run_id}/approve", "post"),
        (f"/agent/runs/{run_b.run_id}/reject", "post"),
        (f"/agent/runs/{run_b.run_id}/execute", "post"),
        (f"/agent/runs/{run_b.run_id}/events", "get"),
    ):
        missing_id = f"missing-{uuid.uuid4().hex[:12]}"
        missing_endpoint = endpoint.replace(run_b.run_id, missing_id)

        if method == "post":
            response = await client.post(
                endpoint, json={"note": None}, headers=headers
            )
            missing = await client.post(
                missing_endpoint, json={"note": None}, headers=headers
            )
        else:
            response = await client.get(endpoint, headers=headers)
            missing = await client.get(missing_endpoint, headers=headers)

        # Both are 404. The detail echoes the provided run_id via the
        # standard "was not found" template — that template is identical
        # for a real-but-foreign run and for a completely missing one,
        # which is exactly the non-enumerating guarantee: an attacker
        # cannot distinguish "your org does not own this run" from
        # "this run does not exist."
        assert response.status_code == 404, endpoint
        assert missing.status_code == 404, endpoint
        assert response.json()["detail"].endswith("was not found.") or response.json()["detail"].endswith("was not found")
        assert missing.json()["detail"].endswith("was not found.") or missing.json()["detail"].endswith("was not found")

    # No approval/execution state may have changed on threshold foreign run.
    reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, run_b.run_id)
    assert reloaded is not None
    assert reloaded.status == "pending_approval"
    assert reloaded.reviewer_note is None
    assert reloaded.reviewed_at is None


# ===================================================================
# C. analyze/start-run on a foreign ticket is rejected
# ===================================================================


@pytest.mark.asyncio
async def test_c_cannot_analyze_foreign_ticket(db, client, two_orgs, fake_llm):
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](
        org_b.id,
        subject="Beta refund policy question",
    )
    fake_llm(_respond_decision())

    response = await client.post(
        f"/agent/tickets/{ticket_b.id}/analyze",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 404
    assert "was not found" in response.json()["detail"]

    # No run may have been created against the foreign ticket.
    found = await db.execute(
        select(AgentRun).where(AgentRun.ticket_id == ticket_b.id)
    )
    assert found.scalar_one_or_none() is None


# ===================================================================
# D. new run inherits ownership from the persisted ticket
# ===================================================================


@pytest.mark.asyncio
async def test_d_new_run_inherits_ticket_org(
    db, client, two_orgs, fake_llm, monkeypatch
):
    org_a = two_orgs["org_a"]

    async def _no_knowledge(
        db, *, organization_id, query, limit=5
    ) -> list[dict]:
        return []

    monkeypatch.setattr(KnowledgeSearchService, "search", _no_knowledge)

    ticket_a = await two_orgs["make_ticket"](
        org_a.id,
        subject="Alpha refund policy question",
    )
    fake_llm(_respond_decision())

    response = await client.post(
        f"/agent/tickets/{ticket_a.id}/analyze",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 200

    body = response.json()
    run = await AgentRunRepository.get_by_run_id_unscoped(db, body["run_id"])
    assert run is not None
    assert run.organization_id == org_a.id
    assert run.ticket_id == ticket_a.id
    assert run.organization_id == ticket_a.organization_id


# ===================================================================
# E. cannot approve a foreign run
# ===================================================================


@pytest.mark.asyncio
async def test_e_cannot_approve_foreign_run(db, client, two_orgs):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        action="respond",
        reason="Beta reason.",
    )

    response = await client.post(
        f"/agent/runs/{run_b.run_id}/approve",
        json={"note": "Org A reviewer approved"},
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 404

    reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, run_b.run_id)
    assert reloaded.status == "pending_approval"
    assert reloaded.reviewer_note is None

    # Same-tenant approve still works end to end.
    ticket_a = await two_orgs["make_ticket"](org_a.id, subject="Alpha ticket")
    run_a = await two_orgs["make_run"](
        org_a.id, ticket_a.id, action="respond", reason="Alpha reason."
    )
    response = await client.post(
        f"/agent/runs/{run_a.run_id}/approve",
        json={"note": "Org A reviewer approved"},
        headers=_auth_headers(USER_ALPHA),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["reviewer_note"] == "Org A reviewer approved"


# ===================================================================
# F. cannot reject a foreign run
# ===================================================================


@pytest.mark.asyncio
async def test_f_cannot_reject_foreign_run(db, client, two_orgs):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        action="respond",
        reason="Beta reason.",
    )

    response = await client.post(
        f"/agent/runs/{run_b.run_id}/reject",
        json={"note": "Org A reviewer rejected"},
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 404

    reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, run_b.run_id)
    assert reloaded.status == "pending_approval"
    assert reloaded.reviewer_note is None

    ticket_a = await two_orgs["make_ticket"](org_a.id, subject="Alpha ticket")
    run_a = await two_orgs["make_run"](
        org_a.id, ticket_a.id, action="respond", reason="Alpha reason."
    )
    response = await client.post(
        f"/agent/runs/{run_a.run_id}/reject",
        json={"note": "Org A reviewer rejected"},
        headers=_auth_headers(USER_ALPHA),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


# ===================================================================
# G. cannot execute a foreign run
# ===================================================================


@pytest.mark.asyncio
async def test_g_cannot_execute_foreign_run(db, client, two_orgs):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](
        org_b.id,
        subject="Beta ticket",
        external_id="900000777",
        source="zendesk",
    )
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        status="approved",
        action="internal_note",
        plan=[
            {
                "tool": "zendesk.add_internal_note",
                "arguments": {"reason": "Beta note."},
                "requires_approval": False,
                "authorized": True,
                "risk_level": "low",
            }
        ],
    )

    response = await client.post(
        f"/agent/runs/{run_b.run_id}/execute",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 404

    reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, run_b.run_id)
    assert reloaded.status == "approved"

    jobs = await db.execute(
        select(IntegrationJob).where(
            IntegrationJob.organization_id.in_([org_a.id, org_b.id])
        )
    )
    assert jobs.scalars().all() == []


# ===================================================================
# H. foreign run drafts / reasons / reviewer notes never disclosed
# ===================================================================


@pytest.mark.asyncio
async def test_h_foreign_run_sensitive_fields_never_disclosed(
    db, client, two_orgs
):
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        action="respond",
        reason="HIGHLY-SENSITIVE-BETA-REASON",
        response_draft="HIGHLY-SENSITIVE-BETA-DRAFT",
        reviewer_note="HIGHLY-SENSITIVE-BETA-REVIEWER-NOTE",
    )

    headers = _auth_headers(USER_ALPHA)

    # The tenant run list never contains the foreign run.
    list_response = await client.get("/agent/runs", headers=headers)
    assert list_response.status_code == 200
    for secret in (
        "HIGHLY-SENSITIVE-BETA-REASON",
        "HIGHLY-SENSITIVE-BETA-DRAFT",
        "HIGHLY-SENSITIVE-BETA-REVIEWER-NOTE",
    ):
        assert secret not in list_response.text

    # No foreign-ID surface discloses them either (all 404, no body data).
    response = await client.post(
        f"/agent/runs/{run_b.run_id}/approve",
        json={"note": None},
        headers=headers,
    )
    assert response.status_code == 404
    for secret in (
        "HIGHLY-SENSITIVE-BETA-REASON",
        "HIGHLY-SENSITIVE-BETA-DRAFT",
        "HIGHLY-SENSITIVE-BETA-REVIEWER-NOTE",
    ):
        assert secret not in response.text

    events = await client.get(
        f"/agent/runs/{run_b.run_id}/events",
        headers=headers,
    )
    assert events.status_code == 404
    for secret in (
        "HIGHLY-SENSITIVE-BETA-REASON",
        "HIGHLY-SENSITIVE-BETA-DRAFT",
        "HIGHLY-SENSITIVE-BETA-REVIEWER-NOTE",
    ):
        assert secret not in events.text


# ===================================================================
# I. valid Org A execution affects only Org A ticket
# ===================================================================


@pytest.mark.asyncio
async def test_i_own_execution_scoped_to_own_ticket_org(
    db, two_orgs, monkeypatch
):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_a = await two_orgs["make_ticket"](
        org_a.id,
        subject="Alpha ticket",
        external_id="1001",
        source="zendesk",
    )
    ticket_b = await two_orgs["make_ticket"](
        org_b.id,
        subject="Beta ticket",
        external_id="2002",
        source="zendesk",
    )

    run_a_id = uuid.uuid4().hex
    run_a_meta = _authorized_internal_note_run(
        run_a_id,
        organization_id=org_a.id,
        ticket_id=ticket_a.id,
        reason="Alpha internal note.",
    )
    run_a = await two_orgs["make_run"](
        org_a.id,
        ticket_a.id,
        run_id=run_a_id,
        status="approved",
        action="internal_note",
        plan=run_a_meta["plan"],
        tool_policy_version=run_a_meta["tool_policy_version"],
        authorization_source=run_a_meta["authorization_source"],
        authorized_by_subject=run_a_meta["authorized_by_subject"],
        authorization_digest=run_a_meta["authorization_digest"],
    )
    run_b_id = uuid.uuid4().hex
    run_b_meta = _authorized_internal_note_run(
        run_b_id,
        organization_id=org_b.id,
        ticket_id=ticket_b.id,
        reason="Beta internal note.",
    )
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        run_id=run_b_id,
        status="approved",
        action="internal_note",
        plan=run_b_meta["plan"],
        tool_policy_version=run_b_meta["tool_policy_version"],
        authorization_source=run_b_meta["authorization_source"],
        authorized_by_subject=run_b_meta["authorized_by_subject"],
        authorization_digest=run_b_meta["authorization_digest"],
    )

    captured: dict = {"orgs": [], "paths": [], "sync_orgs": []}

    async def fake_request(
        _self, db, method, path, *, organization_id, retry_on_unauthorized=True, **kwargs
    ):
        captured["orgs"].append(organization_id)
        captured["paths"].append(path)
        return {"comments": []}

    async def fake_sync(db, zendesk_ticket_id, organization_id):
        captured["sync_orgs"].append(organization_id)

    from app.integrations.zendesk.client import ZendeskClient

    monkeypatch.setattr(ZendeskClient, "request", fake_request)
    monkeypatch.setattr(
        ZendeskSyncService, "sync_ticket_for_tenant", fake_sync
    )

    result = await agent_execution_service.execute(
        db=db,
        run_id=run_a.run_id,
        organization_id=org_a.id,
    )

    assert result["status"] == "executed"
    assert captured["orgs"]
    assert all(organization_id == org_a.id for organization_id in captured["orgs"])
    assert all(organization_id == org_a.id for organization_id in captured["sync_orgs"])

    # Org A's external ticket id was addressed; Org B's ticket was never hit.
    assert any("/tickets/1001" in path for path in captured["paths"])
    assert not any("/tickets/2002" in path for path in captured["paths"])

    reloaded_b = await AgentRunRepository.get_by_run_id_unscoped(db, run_b.run_id)
    assert reloaded_b.status == "approved"
    assert reloaded_b.executed_at is None


# ===================================================================
# J. database rejects run/ticket organization mismatch
# ===================================================================


@pytest.mark.asyncio
async def test_j_run_ticket_org_mismatch_rejected(db, two_orgs):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")

    mismatched = AgentRun(
        run_id=uuid.uuid4().hex,
        ticket_id=ticket_b.id,
        organization_id=org_a.id,
        action="respond",
        reason="Mismatched ownership attempt.",
        status="pending_approval",
        sources=[],
        workflow_path=[],
        tool_plan=[],
    )
    db.add(mismatched)

    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()

    # Nothing of the mismatched run persists.
    remaining = await db.execute(
        select(AgentRun).where(AgentRun.run_id == mismatched.run_id)
    )
    assert remaining.scalar_one_or_none() is None


# ===================================================================
# K. legacy NULL-org run is inert
# ===================================================================


@pytest.mark.asyncio
async def test_k_legacy_null_org_run_inert(db, client, two_orgs):
    legacy_ticket = await two_orgs["make_ticket"](
        None, subject="Legacy unowned ticket"
    )
    legacy_run = await two_orgs["make_run"](
        None,
        legacy_ticket.id,
        action="respond",
        reason="Legacy reason.",
        response_draft="Legacy draft.",
    )

    headers = _auth_headers(USER_ALPHA)

    listed = await client.get("/agent/runs", headers=headers)
    assert listed.status_code == 200
    assert legacy_run.run_id not in listed.text

    for endpoint, method in (
        (f"/agent/runs/{legacy_run.run_id}/approve", "post"),
        (f"/agent/runs/{legacy_run.run_id}/reject", "post"),
        (f"/agent/runs/{legacy_run.run_id}/execute", "post"),
        (f"/agent/runs/{legacy_run.run_id}/events", "get"),
    ):
        if method == "post":
            response = await client.post(
                endpoint, json={"note": None}, headers=headers
            )
        else:
            response = await client.get(endpoint, headers=headers)
        assert response.status_code == 404, endpoint

    # Direct repository writes also refuse the NULL-org run.
    reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, legacy_run.run_id)
    with pytest.raises(ValueError):
        await AgentRunRepository.mark_executed(db, reloaded)

    reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, legacy_run.run_id)
    assert reloaded.status == "pending_approval"


# ===================================================================
# L. forged tenant header -> 403 before any agent surface
# ===================================================================


@pytest.mark.asyncio
async def test_l_forged_tenant_header_403(db, client, two_orgs):
    org_b = two_orgs["org_b"]

    response = await client.get(
        "/agent/runs",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_b.id),
    )

    assert response.status_code == 403
    assert "Organization membership not found" in response.text


# ===================================================================
# M. multi-membership without selector -> 409
# ===================================================================


@pytest.mark.asyncio
async def test_m_multi_membership_without_selector_409(
    db, client, two_orgs
):
    org_b = two_orgs["org_b"]

    await two_orgs["add_membership"](USER_ALPHA, org_b.id)

    response = await client.get(
        "/agent/runs",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 409
    assert "Multiple organization memberships" in response.text


# ===================================================================
# N. valid selector scopes runs to selected org
# ===================================================================


@pytest.mark.asyncio
async def test_n_valid_selector_scopes_runs(db, client, two_orgs):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]
    await two_orgs["add_membership"](USER_ALPHA, org_b.id)

    ticket_a = await two_orgs["make_ticket"](org_a.id, subject="Alpha ticket")
    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")

    run_a = await two_orgs["make_run"](
        org_a.id, ticket_a.id, action="respond", reason="Alpha reason."
    )
    run_b = await two_orgs["make_run"](
        org_b.id, ticket_b.id, action="respond", reason="Beta reason."
    )

    response = await client.get(
        "/agent/runs",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_b.id),
    )

    assert response.status_code == 200
    run_ids = [item["run_id"] for item in response.json()]
    assert run_b.run_id in run_ids
    assert run_a.run_id not in run_ids
    assert all(item.get("organization_id") == org_b.id for item in response.json())


# ===================================================================
# O. agent RAG retrieval uses the run/ticket organization
# ===================================================================


@pytest.mark.asyncio
async def test_o_rag_retrieval_scoped_to_run_org(
    db, client, two_orgs, fake_llm, monkeypatch
):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    captured: dict = {"organization_ids": []}

    async def fake_search(
        db, *, organization_id, query, limit=5
    ) -> list[dict]:
        captured["organization_ids"].append(organization_id)
        return []

    monkeypatch.setattr(KnowledgeSearchService, "search", fake_search)

    ticket_a = await two_orgs["make_ticket"](
        org_a.id,
        subject="Refund policy question",
        description="How long do refunds take?",
    )
    fake_llm(_respond_decision())

    response = await client.post(
        f"/agent/tickets/{ticket_a.id}/analyze",
        headers=_auth_headers(USER_ALPHA),
    )
    assert response.status_code == 200

    assert captured["organization_ids"] == [org_a.id]
    assert org_b.id not in captured["organization_ids"]


# ===================================================================
# P. agent Zendesk execution selects the tenant-owned connection
# ===================================================================


@pytest.mark.asyncio
async def test_p_zendesk_execution_selects_tenant_connection(
    db, two_orgs, monkeypatch
):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](
        org_b.id,
        subject="Beta ticket",
        external_id="2002",
        source="zendesk",
    )
    run_b_id = uuid.uuid4().hex
    run_b_meta = _authorized_internal_note_run(
        run_b_id,
        organization_id=org_b.id,
        ticket_id=ticket_b.id,
        reason="Beta internal note.",
    )
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        run_id=run_b_id,
        status="approved",
        action="internal_note",
        plan=run_b_meta["plan"],
        tool_policy_version=run_b_meta["tool_policy_version"],
        authorization_source=run_b_meta["authorization_source"],
        authorized_by_subject=run_b_meta["authorized_by_subject"],
        authorization_digest=run_b_meta["authorization_digest"],
    )

    captured: dict = {"orgs": []}

    async def fake_request(
        _self, db, method, path, *, organization_id, retry_on_unauthorized=True, **kwargs
    ):
        captured["orgs"].append(organization_id)
        return {"comments": []}

    async def fake_sync(db, zendesk_ticket_id, organization_id):
        captured["orgs"].append(organization_id)

    from app.integrations.zendesk.client import ZendeskClient

    monkeypatch.setattr(ZendeskClient, "request", fake_request)
    monkeypatch.setattr(
        ZendeskSyncService, "sync_ticket_for_tenant", fake_sync
    )

    result = await agent_execution_service.execute(
        db=db,
        run_id=run_b.run_id,
        organization_id=org_b.id,
    )

    assert result["status"] == "executed"
    assert captured["orgs"]
    # Every external credential lookup was for Org B; Org A never appeared.
    assert all(organization_id == org_b.id for organization_id in captured["orgs"])
    assert org_a.id not in captured["orgs"]


# ===================================================================
# Q. event stream foreign-run access rejected
# ===================================================================


@pytest.mark.asyncio
async def test_q_events_foreign_run_rejected(db, client, two_orgs):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](org_b.id, subject="Beta ticket")
    run_b = await two_orgs["make_run"](
        org_b.id, ticket_b.id, action="respond", reason="Beta reason."
    )

    foreign = await client.get(
        f"/agent/runs/{run_b.run_id}/events",
        headers=_auth_headers(USER_ALPHA),
    )
    missing = await client.get(
        f"/agent/runs/{uuid.uuid4().hex}/events",
        headers=_auth_headers(USER_ALPHA),
    )

    assert foreign.status_code == 404
    assert missing.status_code == 404
    # Both use the same "was not found" template — an attacker cannot
    # distinguish "your org does not own this run" from "doesn't exist."
    assert "was not found" in foreign.json()["detail"]
    assert "was not found" in missing.json()["detail"]

    # Owned run events remain readable.
    ticket_a = await two_orgs["make_ticket"](org_a.id, subject="Alpha ticket")
    run_a = await two_orgs["make_run"](
        org_a.id, ticket_a.id, action="respond", reason="Alpha reason."
    )
    owned = await client.get(
        f"/agent/runs/{run_a.run_id}/events",
        headers=_auth_headers(USER_ALPHA),
    )
    assert owned.status_code == 200


# ===================================================================
# R. tenant APIs never call *_unscoped repository methods
# ===================================================================


def test_r_tenant_apis_never_call_unscoped_methods():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    repo_src = (repo_root / "app/repositories/agent_run_repository.py").read_text()

    assert "def get_by_run_id_unscoped" in repo_src
    assert "def claim_for_execution_unscoped" in repo_src

    route_src = inspect.getsource(agent_route_module)

    # Tenant-facing routes only reference tenant-scoped lookups.
    assert "get_by_run_id_for_tenant(" in route_src
    assert "list_runs_for_tenant(" in route_src
    assert "list_events(" in route_src
    assert "get_by_run_id(" not in route_src
    assert "list_runs(" not in route_src
    assert "claim_for_execution(" not in route_src
    assert "get_by_run_id_unscoped" not in route_src
    assert "get_pending_run_for_tenant(" not in route_src

    for module in (
        agent_workflow_service_module,
        integration_job_service_module,
        agent_approval_service_module,
        agent_execution_service_module,
    ):
        src = inspect.getsource(module)
        # No module may reference the old unscoped lookup by exact name.
        # The underscore-prefixed variants exist for the worker only.
        assert "get_by_run_id(" not in src
        assert "list_runs(" not in src
        assert "claim_for_execution(" not in src


# ===================================================================
# Cross-tenant side-effect regression (Phase 1C.3C section 16)
# ===================================================================


@pytest.mark.asyncio
async def test_cross_tenant_execution_side_effect_blocked(
    db, client, two_orgs, monkeypatch
):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_b = await two_orgs["make_ticket"](
        org_b.id,
        subject="Beta ticket",
        description="Beta description.",
        external_id="2002",
        source="zendesk",
    )
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        status="approved",
        action="internal_note",
        reason="Beta internal note reason.",
        plan=[
            {
                "tool": "zendesk.add_internal_note",
                "arguments": {"reason": "Beta internal note."},
                "requires_approval": False,
                "authorized": True,
                "risk_level": "low",
            }
        ],
    )

    mutation_calls: list = []

    async def fake_request(
        _self, db, method, path, *, organization_id, retry_on_unauthorized=True, **kwargs
    ):
        mutation_calls.append((method, path, organization_id))
        return {"comments": []}

    from app.integrations.zendesk.client import ZendeskClient

    monkeypatch.setattr(ZendeskClient, "request", fake_request)

    # Org A attempts to execute an Org B run through the human route.
    response = await client.post(
        f"/agent/runs/{run_b.run_id}/execute",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 404

    missing = await client.post(
        f"/agent/runs/{uuid.uuid4().hex}/execute",
        headers=_auth_headers(USER_ALPHA),
    )
    # Both use the standard "was not found" template — the attacker
    # cannot distinguish "foreign run" from "missing run."
    assert "was not found" in response.json()["detail"]
    assert "was not found" in missing.json()["detail"]
    assert "Beta internal note" not in response.text

    # Org B's Zendesk fake received zero calls.
    assert mutation_calls == []

    # Org B's ticket is unchanged.
    ticket_reloaded = await db.get(Ticket, ticket_b.id)
    assert ticket_reloaded is not None
    assert ticket_reloaded.organization_id == org_b.id
    assert ticket_reloaded.subject == "Beta ticket"
    assert ticket_reloaded.description == "Beta description."

    # Org B's run state is unchanged; no iterator approval/execution side effect.
    run_reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, run_b.run_id)
    assert run_reloaded.status == "approved"
    assert run_reloaded.executed_at is None
    assert run_reloaded.reviewer_note is None

    # No agent-execution job was queued for anyone.
    jobs = await db.execute(
        select(IntegrationJob).where(
            IntegrationJob.organization_id.in_([org_a.id, org_b.id])
        )
    )
    assert jobs.scalars().all() == []


# ===================================================================
# Tenant-consistent compose: analyze -> approve -> execute for own org
# ===================================================================


@pytest.mark.asyncio
async def test_own_run_full_cycle_analyze_approve_execute(
    db, two_orgs, monkeypatch
):
    org_a = two_orgs["org_a"]

    ticket_a = await two_orgs["make_ticket"](
        org_a.id,
        subject="Alpha ticket",
        external_id="1001",
        source="zendesk",
    )
    plan = [
        {
            "tool": "zendesk.add_internal_note",
            "arguments": {"reason": "Alpha note."},
            "requires_approval": False,
            "authorized": True,
            "risk_level": "low",
            "required_capability": "ticket.write",
        }
    ]
    run_a = await two_orgs["make_run"](
        org_a.id,
        ticket_a.id,
        status="pending_approval",
        action="internal_note",
        plan=plan,
    )

    captured: dict = {"orgs": []}

    async def fake_request(
        _self, db, method, path, *, organization_id, retry_on_unauthorized=True, **kwargs
    ):
        captured["orgs"].append(organization_id)
        return {"comments": []}

    async def fake_sync(db, zendesk_ticket_id, organization_id):
        captured["orgs"].append(organization_id)

    from app.integrations.zendesk.client import ZendeskClient

    monkeypatch.setattr(ZendeskClient, "request", fake_request)
    monkeypatch.setattr(
        ZendeskSyncService, "sync_ticket_for_tenant", fake_sync
    )

    run = await AgentRunRepository.mark_auto_approved(db, run_a)
    assert run.status == "approved"

    run.tool_policy_version = ToolAuthorizationService.TOOL_POLICY_VERSION
    run.authorization_source = "policy_auto"
    run.authorized_by_subject = "cxops-test"
    run.authorization_digest = ToolAuthorizationService.compute_run_digest(
        run_id=run.run_id,
        organization_id=org_a.id,
        ticket_id=ticket_a.id,
        policy_version=run.tool_policy_version,
        tool_plan=plan,
    )
    await db.commit()

    result = await agent_execution_service.execute(
        db=db,
        run_id=run.run_id,
        organization_id=org_a.id,
    )
    assert result["status"] == "executed"
    assert captured["orgs"]
    assert all(organization_id == org_a.id for organization_id in captured["orgs"])

# ===================================================================
# Phase 1E.3.1: analysis idempotency concurrency guard
# ===================================================================


class _MockAdvisoryLocks:
    """In-memory per-key asyncio locks used to unit-test the concurrency guard.

    This replaces the Postgres advisory-lock primitives so tests prove the
    critical-section logic without depending on connection-pool sizing or DB
    timing. Keys are still scoped exactly like production, so different
    tickets/orgs/fingerprints remain independent.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, lock_key: int) -> asyncio.Lock:
        return self._locks.setdefault(lock_key, asyncio.Lock())

    async def acquire(self, _db, lock_key: int) -> None:
        await self._lock_for(lock_key).acquire()

    async def release(self, _db, lock_key: int) -> None:
        lock = self._lock_for(lock_key)
        if lock.locked():
            lock.release()


@pytest.fixture
def advisory_lock_mock(monkeypatch):
    """Patch advisory-lock helpers with deterministic per-key asyncio locks."""
    mock = _MockAdvisoryLocks()
    monkeypatch.setattr(
        agent_workflow_service,
        "_acquire_analysis_lock",
        mock.acquire,
    )
    monkeypatch.setattr(
        agent_workflow_service,
        "_release_analysis_lock",
        mock.release,
    )
    return mock


class _CountingFakeDecisionLLM:
    """Deterministic LLM that tracks how many times it was invoked."""

    def __init__(self, decision: AgentDecision) -> None:
        self._decision = decision
        self.invocation_count = 0

    async def ainvoke(self, _messages: list) -> dict:
        self.invocation_count += 1
        raw = types.SimpleNamespace(usage_metadata=None)
        return {
            "raw": raw,
            "parsed": self._decision,
            "parsing_error": None,
        }


@pytest.fixture
def counting_llm(monkeypatch):
    """Install a counting fake decision model and return its counter."""

    def _install(decision: AgentDecision) -> _CountingFakeDecisionLLM:
        fake = _CountingFakeDecisionLLM(decision)
        monkeypatch.setattr(
            agent_workflow_service,
            "decision_llm",
            fake,
        )
        return fake

    return _install


async def _analyze_ticket_directly(
    ticket_id: int,
    organization_id: int,
    *,
    force: bool = False,
) -> dict:
    """Call the workflow service directly with a fresh database session."""
    async with AsyncSessionLocal() as session:
        return await agent_workflow_service.analyze(
            session,
            ticket_id=ticket_id,
            organization_id=organization_id,
            authz=None,
            force=force,
        )


@pytest.mark.asyncio
async def test_analysis_lock_key_is_tenant_and_ticket_scoped():
    """The advisory-lock key changes when tenant, ticket, or fingerprint change."""
    key_same_1 = agent_workflow_service._analysis_lock_key(
        organization_id=1,
        ticket_id=10,
        fingerprint="abc",
    )
    key_same_2 = agent_workflow_service._analysis_lock_key(
        organization_id=1,
        ticket_id=10,
        fingerprint="abc",
    )
    key_diff_org = agent_workflow_service._analysis_lock_key(
        organization_id=2,
        ticket_id=10,
        fingerprint="abc",
    )
    key_diff_ticket = agent_workflow_service._analysis_lock_key(
        organization_id=1,
        ticket_id=11,
        fingerprint="abc",
    )
    key_diff_fingerprint = agent_workflow_service._analysis_lock_key(
        organization_id=1,
        ticket_id=10,
        fingerprint="def",
    )

    assert key_same_1 == key_same_2
    assert key_diff_org != key_same_1
    assert key_diff_ticket != key_same_1
    assert key_diff_fingerprint != key_same_1
    assert -(2**63) <= key_same_1 <= 2**63 - 1


@pytest.mark.asyncio
async def test_concurrent_non_force_analysis_reuses_one_run(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """Two simultaneous identical analyses yield one LLM call and one run."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Concurrency test response.",
        requires_human_approval=True,
        response_draft="Draft for concurrency test.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Concurrency test ticket",
        description="Please help with this issue.",
    )

    results = await asyncio.gather(
        _analyze_ticket_directly(ticket.id, org_a.id),
        _analyze_ticket_directly(ticket.id, org_a.id),
    )

    assert fake.invocation_count == 1
    run_ids = {result["run_id"] for result in results}
    assert len(run_ids) == 1
    assert sum(1 for r in results if r["reused"]) == 1
    assert sum(1 for r in results if not r["reused"]) == 1

    async with AsyncSessionLocal() as session:
        runs = await AgentRunRepository.list_runs_for_tenant(
            session,
            organization_id=org_a.id,
        )
    ticket_runs = [run for run in runs if run.ticket_id == ticket.id]
    assert len(ticket_runs) == 1


@pytest.mark.asyncio
async def test_concurrent_three_plus_requests_coalesce(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """Three+ concurrent identical analyses coalesce into a single run."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Three-way concurrency test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Three-way concurrency ticket",
        description="Help needed.",
    )

    results = await asyncio.gather(
        _analyze_ticket_directly(ticket.id, org_a.id),
        _analyze_ticket_directly(ticket.id, org_a.id),
        _analyze_ticket_directly(ticket.id, org_a.id),
    )

    assert fake.invocation_count == 1
    run_ids = {result["run_id"] for result in results}
    assert len(run_ids) == 1
    assert sum(1 for r in results if r["reused"]) == 2


@pytest.mark.asyncio
async def test_concurrent_different_tickets_do_not_block(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """Different tickets are independent; locks do not serialize them."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Different ticket test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket_a = await two_orgs["make_ticket"](
        org_a.id,
        subject="Ticket A",
        description="Help A.",
    )
    ticket_b = await two_orgs["make_ticket"](
        org_a.id,
        subject="Ticket B",
        description="Help B.",
    )

    results = await asyncio.gather(
        _analyze_ticket_directly(ticket_a.id, org_a.id),
        _analyze_ticket_directly(ticket_b.id, org_a.id),
    )

    assert fake.invocation_count == 2
    assert results[0]["run_id"] != results[1]["run_id"]
    assert results[0]["reused"] is False
    assert results[1]["reused"] is False


@pytest.mark.asyncio
async def test_concurrent_same_ticket_different_orgs_do_not_share(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """Tenant boundary in the lock key prevents cross-tenant serialization."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Cross-tenant concurrency test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_a = await two_orgs["make_ticket"](
        org_a.id,
        subject="Shared content ticket",
        description="Same issue.",
    )
    ticket_b = await two_orgs["make_ticket"](
        org_b.id,
        subject="Shared content ticket",
        description="Same issue.",
    )

    results = await asyncio.gather(
        _analyze_ticket_directly(ticket_a.id, org_a.id),
        _analyze_ticket_directly(ticket_b.id, org_b.id),
    )

    assert fake.invocation_count == 2
    assert results[0]["run_id"] != results[1]["run_id"]
    assert results[0]["reused"] is False
    assert results[1]["reused"] is False


@pytest.mark.asyncio
async def test_changed_fingerprint_gets_fresh_analysis(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """A changed ticket context produces a new analysis, not a reuse."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Fingerprint change test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Original subject",
        description="Original description.",
    )

    first_body = await _analyze_ticket_directly(ticket.id, org_a.id)
    assert first_body["reused"] is False

    async with AsyncSessionLocal() as session:
        ticket_to_update = await session.get(Ticket, ticket.id)
        ticket_to_update.subject = "Changed subject"
        ticket_to_update.description = "Changed description."
        await session.commit()

    second_body = await _analyze_ticket_directly(ticket.id, org_a.id)

    assert fake.invocation_count == 2
    assert second_body["run_id"] != first_body["run_id"]
    assert second_body["reused"] is False


@pytest.mark.asyncio
async def test_force_reanalysis_creates_fresh_run(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """force=true bypasses reuse and creates a new analysis run."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Force re-analysis test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Force test ticket",
        description="Help.",
    )

    first_body = await _analyze_ticket_directly(ticket.id, org_a.id)

    second_body = await _analyze_ticket_directly(
        ticket.id, org_a.id, force=True
    )

    assert fake.invocation_count == 2
    assert second_body["run_id"] != first_body["run_id"]
    assert first_body["reused"] is False
    assert second_body["reused"] is False

    async with AsyncSessionLocal() as session:
        first_run = await AgentRunRepository.get_by_run_id_for_tenant(
            session,
            first_body["run_id"],
            org_a.id,
        )
        assert first_run is not None
        await session.refresh(first_run)
        assert first_run.status == "superseded"


@pytest.mark.asyncio
async def test_llm_failure_releases_lock_and_allows_retry(
    advisory_lock_mock,
    two_orgs,
    monkeypatch,
):
    """A failed LLM call under the lock does not leave a stale reusable run."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    class _FailingThenSucceedingLLM:
        def __init__(self) -> None:
            self.attempts = 0

        async def ainvoke(self, _messages: list) -> dict:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("Simulated LLM failure")
            decision = AgentDecision(
                action="respond",
                reason="Retry success.",
                requires_human_approval=True,
                response_draft="Draft.",
            )
            raw = types.SimpleNamespace(usage_metadata=None)
            return {
                "raw": raw,
                "parsed": decision,
                "parsing_error": None,
            }

    monkeypatch.setattr(
        agent_workflow_service,
        "decision_llm",
        _FailingThenSucceedingLLM(),
    )

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="LLM failure retry ticket",
        description="Help.",
    )

    with pytest.raises(RuntimeError):
        await _analyze_ticket_directly(ticket.id, org_a.id)

    second_body = await _analyze_ticket_directly(ticket.id, org_a.id)
    assert second_body["reused"] is False

    async with AsyncSessionLocal() as session:
        runs = await AgentRunRepository.list_runs_for_tenant(
            session,
            organization_id=org_a.id,
        )
    ticket_runs = [run for run in runs if run.ticket_id == ticket.id]
    assert len(ticket_runs) == 1


@pytest.mark.asyncio
async def test_coalesced_request_does_not_duplicate_telemetry(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """A coalesced concurrent request creates a single AI observability row."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Telemetry test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Telemetry concurrency ticket",
        description="Help.",
    )

    await asyncio.gather(
        _analyze_ticket_directly(ticket.id, org_a.id),
        _analyze_ticket_directly(ticket.id, org_a.id),
    )

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AIRequestLog).where(
                AIRequestLog.organization_id == org_a.id,
                AIRequestLog.feature == "agent_decision",
            )
        )
        logs = result.scalars().all()
    ticket_logs = [
        log
        for log in logs
        if log.question and "Telemetry concurrency ticket" in log.question
    ]
    assert len(ticket_logs) == 1


@pytest.mark.asyncio
async def test_historical_runs_preserved_after_reuse(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """Reusing a current run does not delete historical superseded runs."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Historical preservation test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Historical preservation ticket",
        description="Help.",
    )

    first_body = await _analyze_ticket_directly(ticket.id, org_a.id)
    second_body = await _analyze_ticket_directly(
        ticket.id, org_a.id, force=True
    )
    third_body = await _analyze_ticket_directly(ticket.id, org_a.id)

    assert third_body["run_id"] == second_body["run_id"]
    assert third_body["reused"] is True
    assert first_body["run_id"] != second_body["run_id"]

    async with AsyncSessionLocal() as session:
        runs = await AgentRunRepository.list_runs_for_tenant(
            session,
            organization_id=org_a.id,
        )
    ticket_runs = [run for run in runs if run.ticket_id == ticket.id]
    assert len(ticket_runs) == 2
    statuses = {run.status for run in ticket_runs}
    assert statuses == {"superseded", "pending_approval"}


@pytest.mark.asyncio
async def test_concurrent_force_requests_each_create_run(
    advisory_lock_mock,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """Concurrent force=true requests intentionally each create a fresh run."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Concurrent force test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Concurrent force ticket",
        description="Help.",
    )

    results = await asyncio.gather(
        _analyze_ticket_directly(ticket.id, org_a.id, force=True),
        _analyze_ticket_directly(ticket.id, org_a.id, force=True),
    )

    assert fake.invocation_count == 2
    assert results[0]["run_id"] != results[1]["run_id"]
    assert results[0]["reused"] is False
    assert results[1]["reused"] is False


# ===================================================================
# Phase 1E.3.1: real PostgreSQL advisory-lock connection-safety tests
# ===================================================================


@pytest.fixture
def fixed_fingerprint(monkeypatch):
    """Make the analysis fingerprint deterministic for lock-key tests."""
    monkeypatch.setattr(
        agent_workflow_service,
        "_compute_fingerprint",
        lambda **_: "test-fingerprint-fixed",
    )


async def _try_acquire_advisory_lock(lock_key: int) -> bool:
    """Try to acquire the same advisory key on a fresh DB connection."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        acquired = result.scalar()
        if acquired:
            # Release immediately so we don't leak it in the test process.
            await conn.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": lock_key},
            )
        return bool(acquired)


@pytest.mark.asyncio
async def test_real_postgresql_concurrent_non_force_analysis_reuses_one_run(
    fixed_fingerprint,
    two_orgs,
    counting_llm,
    monkeypatch,
):
    """Real advisory locks serialize two concurrent analyses into one run."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    decision = AgentDecision(
        action="respond",
        reason="Real lock concurrency test.",
        requires_human_approval=True,
        response_draft="Draft.",
    )
    fake = counting_llm(decision)

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Real lock ticket",
        description="Help.",
    )

    results = await asyncio.gather(
        _analyze_ticket_directly(ticket.id, org_a.id),
        _analyze_ticket_directly(ticket.id, org_a.id),
    )

    assert fake.invocation_count == 1
    run_ids = {result["run_id"] for result in results}
    assert len(run_ids) == 1
    assert sum(1 for r in results if r["reused"]) == 1

    lock_key = agent_workflow_service._analysis_lock_key(
        organization_id=org_a.id,
        ticket_id=ticket.id,
        fingerprint="test-fingerprint-fixed",
    )
    assert await _try_acquire_advisory_lock(lock_key) is True


@pytest.mark.asyncio
async def test_real_postgresql_lock_released_after_llm_failure(
    fixed_fingerprint,
    two_orgs,
    monkeypatch,
):
    """Real advisory lock is released after an LLM failure inside the section."""

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        KnowledgeSearchService,
        "search",
        _fake_search,
    )

    class _FailingThenSucceedingLLM:
        def __init__(self) -> None:
            self.attempts = 0

        async def ainvoke(self, _messages: list) -> dict:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("Simulated LLM failure")
            decision = AgentDecision(
                action="respond",
                reason="Retry success.",
                requires_human_approval=True,
                response_draft="Draft.",
            )
            raw = types.SimpleNamespace(usage_metadata=None)
            return {
                "raw": raw,
                "parsed": decision,
                "parsing_error": None,
            }

    monkeypatch.setattr(
        agent_workflow_service,
        "decision_llm",
        _FailingThenSucceedingLLM(),
    )

    org_a = two_orgs["org_a"]
    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Real lock failure ticket",
        description="Help.",
    )

    lock_key = agent_workflow_service._analysis_lock_key(
        organization_id=org_a.id,
        ticket_id=ticket.id,
        fingerprint="test-fingerprint-fixed",
    )

    with pytest.raises(RuntimeError):
        await _analyze_ticket_directly(ticket.id, org_a.id)

    # Lock must be released even though the first call raised.
    assert await _try_acquire_advisory_lock(lock_key) is True

    second_body = await _analyze_ticket_directly(ticket.id, org_a.id)
    assert second_body["reused"] is False

    # Lock released again after successful retry.
    assert await _try_acquire_advisory_lock(lock_key) is True

    async with AsyncSessionLocal() as session:
        runs = await AgentRunRepository.list_runs_for_tenant(
            session,
            organization_id=org_a.id,
        )
    ticket_runs = [run for run in runs if run.ticket_id == ticket.id]
    assert len(ticket_runs) == 1


@pytest.mark.asyncio
async def test_external_execution_available_uses_zendesk_allowlist(
    client,
    two_orgs,
):
    """Serialize run advertises external execution only for Zendesk-linked tickets."""
    org_a = two_orgs["org_a"]

    zendesk_linked_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Zendesk linked ticket",
        external_id="123",
        source="zendesk",
    )
    zendesk_missing_external_id_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Zendesk missing external id",
        external_id=None,
        source="zendesk",
    )
    zendesk_non_numeric_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Zendesk non-numeric external id",
        external_id="not-a-number",
        source="zendesk",
    )
    local_demo_no_id_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Local demo ticket without id",
        external_id=None,
        source="control-center-test",
    )
    local_demo_with_id_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Local demo ticket with id",
        external_id="demo-123",
        source="control-center-test",
    )
    api_with_external_id_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="API ticket with external id",
        external_id="456",
        source="api",
    )
    future_source_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Future source ticket",
        external_id="future-789",
        source="future-integration",
    )

    zendesk_linked_run = await two_orgs["make_run"](
        org_a.id,
        zendesk_linked_ticket.id,
        status="approved",
        action="respond",
    )
    zendesk_missing_external_id_run = await two_orgs["make_run"](
        org_a.id,
        zendesk_missing_external_id_ticket.id,
        status="approved",
        action="respond",
    )
    zendesk_non_numeric_run = await two_orgs["make_run"](
        org_a.id,
        zendesk_non_numeric_ticket.id,
        status="approved",
        action="respond",
    )
    local_demo_no_id_run = await two_orgs["make_run"](
        org_a.id,
        local_demo_no_id_ticket.id,
        status="approved",
        action="respond",
    )
    local_demo_with_id_run = await two_orgs["make_run"](
        org_a.id,
        local_demo_with_id_ticket.id,
        status="approved",
        action="respond",
    )
    api_with_external_id_run = await two_orgs["make_run"](
        org_a.id,
        api_with_external_id_ticket.id,
        status="approved",
        action="respond",
    )
    future_source_run = await two_orgs["make_run"](
        org_a.id,
        future_source_ticket.id,
        status="approved",
        action="respond",
    )

    headers = _auth_headers(USER_ALPHA, tenant_id=org_a.id)

    response = await client.get("/agent/runs", headers=headers)
    assert response.status_code == 200
    runs = {run["run_id"]: run for run in response.json()}

    assert (
        runs[zendesk_linked_run.run_id]["external_execution_available"] is True
    )
    assert (
        runs[zendesk_missing_external_id_run.run_id][
            "external_execution_available"
        ]
        is False
    )
    assert (
        runs[zendesk_non_numeric_run.run_id]["external_execution_available"]
        is False
    )
    assert (
        runs[local_demo_no_id_run.run_id]["external_execution_available"]
        is False
    )
    assert (
        runs[local_demo_with_id_run.run_id]["external_execution_available"]
        is False
    )
    assert (
        runs[api_with_external_id_run.run_id]["external_execution_available"]
        is False
    )
    assert (
        runs[future_source_run.run_id]["external_execution_available"] is False
    )


@pytest.mark.asyncio
async def test_external_execution_available_is_tenant_scoped(
    client,
    two_orgs,
):
    """A foreign tenant cannot see or influence another tenant's eligibility flag."""
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    ticket_a = await two_orgs["make_ticket"](
        org_a.id,
        subject="Org A Zendesk ticket",
        external_id="111",
        source="zendesk",
    )
    ticket_b = await two_orgs["make_ticket"](
        org_b.id,
        subject="Org B Zendesk ticket",
        external_id="222",
        source="zendesk",
    )

    run_a = await two_orgs["make_run"](
        org_a.id,
        ticket_a.id,
        status="approved",
        action="respond",
    )
    run_b = await two_orgs["make_run"](
        org_b.id,
        ticket_b.id,
        status="approved",
        action="respond",
    )

    headers_a = _auth_headers(USER_ALPHA, tenant_id=org_a.id)
    headers_b = _auth_headers(USER_BETA, tenant_id=org_b.id)

    response_a = await client.get("/agent/runs", headers=headers_a)
    response_b = await client.get("/agent/runs", headers=headers_b)

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    runs_a = {run["run_id"]: run for run in response_a.json()}
    runs_b = {run["run_id"]: run for run in response_b.json()}

    assert run_a.run_id in runs_a
    assert run_b.run_id not in runs_a
    assert runs_a[run_a.run_id]["external_execution_available"] is True

    assert run_b.run_id in runs_b
    assert run_a.run_id not in runs_b
    assert runs_b[run_b.run_id]["external_execution_available"] is True


@pytest.mark.asyncio
async def test_list_runs_includes_external_execution_available(
    client,
    two_orgs,
):
    """The list endpoint also returns the execution availability flag."""
    org_a = two_orgs["org_a"]

    external_ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="External ticket",
        external_id="789",
        source="zendesk",
    )
    await two_orgs["make_run"](
        org_a.id,
        external_ticket.id,
        status="approved",
        action="respond",
    )

    headers = _auth_headers(USER_ALPHA, tenant_id=org_a.id)
    response = await client.get("/agent/runs", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 1
    assert all(
        "external_execution_available" in run
        for run in body
    )
    assert any(
        run["external_execution_available"] is True
        for run in body
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,external_id",
    [
        ("zendesk", None),
        ("zendesk", "not-a-number"),
        ("control-center-test", "demo-123"),
        ("api", "456"),
        ("future-integration", "future-789"),
    ],
)
async def test_execution_preflight_rejects_unsupported_target(
    db,
    two_orgs,
    monkeypatch,
    source,
    external_id,
):
    """Unsupported execution targets are rejected before claiming or calling Zendesk."""
    from app.integrations.zendesk.client import ZendeskClient
    from app.services.agent_execution_service import AgentExecutionStateError

    org_a = two_orgs["org_a"]

    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject=f"Target test ticket ({source})",
        external_id=external_id,
        source=source,
    )

    run_id = uuid.uuid4().hex
    run_meta = _authorized_internal_note_run(
        run_id,
        organization_id=org_a.id,
        ticket_id=ticket.id,
        reason="Target validation note.",
    )
    run = await two_orgs["make_run"](
        org_a.id,
        ticket.id,
        run_id=run_id,
        status="approved",
        action="internal_note",
        plan=run_meta["plan"],
        tool_policy_version=run_meta["tool_policy_version"],
        authorization_source=run_meta["authorization_source"],
        authorized_by_subject=run_meta["authorized_by_subject"],
        authorization_digest=run_meta["authorization_digest"],
    )

    captured_calls: list = []

    async def fake_request(_self, _db, _method, _path, **_kwargs):
        captured_calls.append((_method, _path))
        return {"comments": []}

    monkeypatch.setattr(ZendeskClient, "request", fake_request)

    with pytest.raises(AgentExecutionStateError):
        await agent_execution_service.execute(
            db=db,
            run_id=run.run_id,
            organization_id=org_a.id,
        )

    assert not captured_calls

    reloaded = await AgentRunRepository.get_by_run_id_unscoped(db, run.run_id)
    assert reloaded.status in {"approved", "execution_failed"}


@pytest.mark.asyncio
async def test_execution_preflight_rejects_non_positive_zendesk_id(
    db,
    two_orgs,
    monkeypatch,
):
    """Zendesk external ids that are not positive integers are rejected."""
    from app.integrations.zendesk.client import ZendeskClient
    from app.services.agent_execution_service import AgentExecutionStateError

    org_a = two_orgs["org_a"]

    ticket = await two_orgs["make_ticket"](
        org_a.id,
        subject="Non-positive Zendesk ticket",
        external_id="0",
        source="zendesk",
    )

    run_id = uuid.uuid4().hex
    run_meta = _authorized_internal_note_run(
        run_id,
        organization_id=org_a.id,
        ticket_id=ticket.id,
        reason="Non-positive id note.",
    )
    run = await two_orgs["make_run"](
        org_a.id,
        ticket.id,
        run_id=run_id,
        status="approved",
        action="internal_note",
        plan=run_meta["plan"],
        tool_policy_version=run_meta["tool_policy_version"],
        authorization_source=run_meta["authorization_source"],
        authorized_by_subject=run_meta["authorized_by_subject"],
        authorization_digest=run_meta["authorization_digest"],
    )

    captured_calls: list = []

    async def fake_request(_self, _db, _method, _path, **_kwargs):
        captured_calls.append((_method, _path))
        return {"comments": []}

    monkeypatch.setattr(ZendeskClient, "request", fake_request)

    with pytest.raises(AgentExecutionStateError):
        await agent_execution_service.execute(
            db=db,
            run_id=run.run_id,
            organization_id=org_a.id,
        )

    assert not captured_calls


@pytest.mark.asyncio
async def test_serialization_and_worker_target_agree(
    client,
    two_orgs,
):
    """The response flag and the worker helper always agree for the same ticket."""
    from app.services.zendesk_target_service import (
        resolve_zendesk_execution_target,
    )

    org_a = two_orgs["org_a"]

    tickets = [
        await two_orgs["make_ticket"](
            org_a.id,
            subject="Zendesk numeric",
            external_id="123",
            source="zendesk",
        ),
        await two_orgs["make_ticket"](
            org_a.id,
            subject="Zendesk missing id",
            external_id=None,
            source="zendesk",
        ),
        await two_orgs["make_ticket"](
            org_a.id,
            subject="API with id",
            external_id="456",
            source="api",
        ),
    ]

    runs = []
    for ticket in tickets:
        run = await two_orgs["make_run"](
            org_a.id,
            ticket.id,
            status="approved",
            action="respond",
        )
        runs.append(run)

    headers = _auth_headers(USER_ALPHA, tenant_id=org_a.id)
    response = await client.get("/agent/runs", headers=headers)
    assert response.status_code == 200

    run_map = {item["run_id"]: item for item in response.json()}

    for ticket, run in zip(tickets, runs):
        target = resolve_zendesk_execution_target(ticket)
        assert (
            run_map[run.run_id]["external_execution_available"]
            == (target is not None)
        )
