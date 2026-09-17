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
from sqlalchemy import delete, select
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
from app.core.database import AsyncSessionLocal
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
    ) -> Ticket:
        ticket = Ticket(
            organization_id=organization_id,
            subject=subject,
            description=description,
            external_id=external_id,
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
        org_b.id, subject="Beta ticket", external_id="900000777"
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
        org_a.id, subject="Alpha ticket", external_id="1001"
    )
    ticket_b = await two_orgs["make_ticket"](
        org_b.id, subject="Beta ticket", external_id="2002"
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
        org_b.id, subject="Beta ticket", external_id="2002"
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
        org_a.id, subject="Alpha ticket", external_id="1001"
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