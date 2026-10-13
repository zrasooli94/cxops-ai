"""Phase 1P.9 — asynchronous, durable public-chat turns.

Real FastAPI app + real PostgreSQL (disposable), deterministic fake decision
model and RAG (no OpenAI call, no cost). Covers acceptance latency, delivery
states, cursor updates, idempotency, recovery, ordering, allowance limits,
tenant isolation, failure handoff, and the worker backstop.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models.ai_request_log import AIRequestLog
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.schemas.agent import AgentDecision
from app.services import public_chat_turn_dispatcher as dispatcher_module
from app.services.agent_workflow_service import agent_workflow_service
from app.services.public_chat_service import (
    DEDUPE_INBOUND_PREFIX,
    PUBLIC_CHAT_TURN_JOB_TYPE,
)
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from tests import test_public_chat as base

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated

NO_ACTION = AgentDecision(
    action="no_action",
    reason="Informational greeting; nothing to do.",
    requires_human_approval=False,
    response_draft="",
)


class SlowDecisionLLM(base._CapturingDecisionLLM):
    """The fake model, with an optional delay and a per-session concurrency probe."""

    def __init__(self, decision: AgentDecision, *, delay: float = 0.0) -> None:
        super().__init__(decision)
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def ainvoke(self, messages: list) -> dict:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return await super().ainvoke(messages)
        finally:
            self.active -= 1


def _stub(monkeypatch, *, delay: float = 0.0, decision: AgentDecision = NO_ACTION):
    async def _no_search(*_args, **_kwargs):
        return []

    from app.services.knowledge_search_service import KnowledgeSearchService

    monkeypatch.setattr(KnowledgeSearchService, "search", _no_search)
    fake = SlowDecisionLLM(decision, delay=delay)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", fake)
    return fake


async def _send(client, token, text, *, client_message_id=None, delivery="async"):
    response = await client.post(
        "/public/chat/messages",
        json={
            "client_message_id": client_message_id or uuid.uuid4().hex,
            "text": text,
            "delivery": delivery,
        },
        headers=base._origin_headers(token),
    )
    return response


async def _updates(client, token, after_id=0):
    response = await client.get(
        f"/public/chat/sessions/updates?after_id={after_id}",
        headers=base._origin_headers(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _jobs(db, org_id):
    return (
        await db.execute(
            select(IntegrationJob).where(
                IntegrationJob.organization_id == org_id,
                IntegrationJob.job_type == PUBLIC_CHAT_TURN_JOB_TYPE,
            )
        )
    ).scalars().all()


async def _setup(db, client, org_scope, **config):
    org = await org_scope()
    org_id = org.id
    _cfg, key = await base._make_widget_config(db, org, **config)
    session = await base._create_session(client, key)
    return org_id, session["session"]["token"]


# ---------------------------------------------------------------------------
# Acceptance and delivery states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_message_is_accepted_without_waiting_for_the_model(
    db, client, org_scope, monkeypatch
):
    fake = _stub(monkeypatch, delay=0.5)
    org_id, token = await _setup(db, client, org_scope)

    started = time.perf_counter()
    response = await _send(client, token, "Hello there")
    accept_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processing_state"] == "accepted"
    assert body["reply"] is None
    assert "_turn_job_id" not in body and "_organization_id" not in body
    assert accept_ms < 500, "acceptance must not wait for the 500 ms model call"

    pending = await _updates(client, token)
    assert [t["state"] for t in pending["turns"]] in (["accepted"], ["processing"])
    assert pending["poll_after_ms"] == 1000

    await turn_dispatcher.drain()
    done = await _updates(client, token)
    directions = [m["direction"] for m in done["messages"]]
    assert directions == ["inbound", "outbound"]
    assert done["turns"] == []
    assert fake.invocation_count == 1
    (job,) = await _jobs(db, org_id)
    assert job.status == "completed"


@pytest.mark.asyncio
async def test_cursor_returns_only_newer_messages(db, client, org_scope, monkeypatch):
    _stub(monkeypatch)
    _org_id, token = await _setup(db, client, org_scope)
    await _send(client, token, "First")
    await turn_dispatcher.drain()
    first = await _updates(client, token)
    cursor = first["cursor"]
    assert len(first["messages"]) == 2

    nothing_new = await _updates(client, token, after_id=cursor)
    assert nothing_new["messages"] == []
    assert nothing_new["poll_after_ms"] == 10000

    await _send(client, token, "Second")
    await turn_dispatcher.drain()
    second = await _updates(client, token, after_id=cursor)
    assert next(m["body"] for m in second["messages"]) == "Second"
    assert all(m["id"] > cursor for m in second["messages"])


@pytest.mark.asyncio
async def test_async_retry_is_idempotent(db, client, org_scope, monkeypatch):
    fake = _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)
    message_id = uuid.uuid4().hex

    a = await _send(client, token, "Same", client_message_id=message_id)
    b = await _send(client, token, "Same", client_message_id=message_id)
    await turn_dispatcher.drain()
    c = await _send(client, token, "Same", client_message_id=message_id)

    assert a.json()["message_id"] == b.json()["message_id"] == c.json()["message_id"]
    assert c.json()["processing_state"] == "completed"
    assert len(await _jobs(db, org_id)) == 1
    assert fake.invocation_count == 1
    state = await _updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


@pytest.mark.asyncio
async def test_sync_contract_is_unchanged_for_old_widgets(db, client, org_scope, monkeypatch):
    _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)
    response = await client.post(
        "/public/chat/messages",
        json={"client_message_id": uuid.uuid4().hex, "text": "Hi"},
        headers=base._origin_headers(token),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["reply"] is not None
    assert body["processing_state"] == "completed"
    assert await _jobs(db, org_id) == []


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lost_dispatch_is_recovered_when_the_visitor_polls(
    db, client, org_scope, monkeypatch
):
    """The API instance restarted right after accepting: the in-process task is
    gone but the durable job is not. The visitor's next poll recovers it."""
    fake = _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        await _send(client, token, "Are you there?")
    monkeypatch.setattr(dispatcher_module, "PENDING_GRACE_SECONDS", 0)

    assert (await _jobs(db, org_id))[0].status == "pending"
    await _updates(client, token)  # triggers recovery
    await turn_dispatcher.drain()

    state = await _updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]
    assert fake.invocation_count == 1
    assert (await _jobs(db, org_id))[0].status == "completed"


@pytest.mark.asyncio
async def test_abandoned_processing_turn_is_reclaimed(db, client, org_scope, monkeypatch):
    _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        await _send(client, token, "Hello?")
    (job,) = await _jobs(db, org_id)
    await db.execute(
        update(IntegrationJob)
        .where(IntegrationJob.id == job.id)
        .values(status="processing", attempts=1, locked_at=datetime.now(UTC) - timedelta(hours=1))
    )
    await db.commit()

    await _updates(client, token)
    await turn_dispatcher.drain()

    await db.refresh(job)
    assert job.status == "completed" and job.attempts == 2
    state = await _updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


@pytest.mark.asyncio
async def test_worker_backstop_processes_a_turn(db, client, org_scope, monkeypatch):
    from scripts import worker

    _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        await _send(client, token, "Hi from the backstop")
    (job,) = await _jobs(db, org_id)
    await db.execute(
        update(IntegrationJob)
        .where(IntegrationJob.id == job.id)
        .values(status="processing", attempts=1, locked_at=datetime.now(UTC))
    )
    await db.commit()
    await db.refresh(job)

    result = await worker.process_claimed_job(db, job)

    assert result.outcome == "completed"
    state = await _updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


# ---------------------------------------------------------------------------
# Ordering and per-session serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turns_of_one_session_run_one_at_a_time_in_order(
    db, client, org_scope, monkeypatch
):
    fake = _stub(monkeypatch, delay=0.1)
    _org_id, token = await _setup(db, client, org_scope)
    for n in range(4):
        await _send(client, token, f"message {n}")
    await turn_dispatcher.drain()

    state = await _updates(client, token)
    inbound = [m for m in state["messages"] if m["direction"] == "inbound"]
    outbound = [m for m in state["messages"] if m["direction"] == "outbound"]
    assert [m["body"] for m in inbound] == [f"message {n}" for n in range(4)]
    assert len(outbound) == 4
    assert fake.max_active == 1, "two turns of one session ran concurrently"


# ---------------------------------------------------------------------------
# Monthly AI allowance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhausted_turn_limit_hands_off_without_a_model_call(
    db, client, org_scope, monkeypatch
):
    fake = _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)
    await db.execute(
        update(PublicChatConfiguration)
        .where(PublicChatConfiguration.organization_id == org_id)
        .values(monthly_ai_turn_limit=0)
    )
    await db.commit()

    await _send(client, token, "Please help")
    await turn_dispatcher.drain()

    assert fake.invocation_count == 0
    state = await _updates(client, token)
    assert state["status"] == "human_requested"
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


@pytest.mark.asyncio
async def test_exhausted_usd_budget_hands_off_and_other_tenants_continue(
    db, client, org_scope, monkeypatch
):
    fake = _stub(monkeypatch)
    noisy_org, noisy_token = await _setup(db, client, org_scope)
    quiet_org, quiet_token = await _setup(db, client, org_scope)
    db.add(
        AIRequestLog(
            organization_id=noisy_org,
            request_id=uuid.uuid4().hex,
            feature="rag_answer",
            model="gpt-4o-mini",
            status="success",
            question="[usage]",
            grounded=False,
            llm_called=True,
            retrieval_count=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_usd=999.0,
            latency_ms=0.0,
            sources=[],
        )
    )
    await db.commit()

    await _send(client, noisy_token, "Noisy")
    await _send(client, quiet_token, "Quiet")
    await turn_dispatcher.drain()

    assert (await _updates(client, noisy_token))["status"] == "human_requested"
    assert (await _updates(client, quiet_token))["status"] == "ai_active"
    assert fake.invocation_count == 1  # only the quiet tenant's turn used the model
    del quiet_org


# ---------------------------------------------------------------------------
# Tenant isolation and failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updates_never_cross_tenants(db, client, org_scope, monkeypatch):
    _stub(monkeypatch)
    _org_a, token_a = await _setup(db, client, org_scope)
    _org_b, token_b = await _setup(db, client, org_scope)
    await _send(client, token_a, "secret from A")
    await _send(client, token_b, "secret from B")
    await turn_dispatcher.drain()

    a = await _updates(client, token_a)
    b = await _updates(client, token_b)
    assert all("from B" not in m["body"] for m in a["messages"])
    assert all("from A" not in m["body"] for m in b["messages"])


@pytest.mark.asyncio
async def test_tampered_turn_payload_cannot_touch_another_tenant(
    db, client, org_scope, monkeypatch
):
    fake = _stub(monkeypatch)
    org_a, token_a = await _setup(db, client, org_scope)
    org_b, token_b = await _setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        await _send(client, token_a, "A")
        await _send(client, token_b, "B")
    (job_a,) = await _jobs(db, org_a)
    (job_b,) = await _jobs(db, org_b)
    # Job bound to tenant A, payload pointing at tenant B's session/message.
    await db.execute(
        update(IntegrationJob)
        .where(IntegrationJob.id == job_a.id)
        .values(payload=job_b.payload, max_attempts=1)
    )
    await db.commit()

    turn_dispatcher.submit(job_a.id, org_a)
    await turn_dispatcher.drain()

    await db.refresh(job_a)
    assert job_a.status == "failed"
    assert fake.invocation_count == 0
    b_state = await _updates(client, token_b)
    assert [m["direction"] for m in b_state["messages"]] == ["inbound"]
    assert b_state["status"] == "ai_active"


@pytest.mark.asyncio
async def test_permanent_turn_failure_hands_off_instead_of_hanging(
    db, client, org_scope, monkeypatch
):
    _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        await _send(client, token, "Hello")
    monkeypatch.setattr(agent_workflow_service, "analyze", _boom)
    (job,) = await _jobs(db, org_id)
    await db.execute(update(IntegrationJob).where(IntegrationJob.id == job.id).values(max_attempts=1))
    await db.commit()

    turn_dispatcher.submit(job.id, org_id)
    await turn_dispatcher.drain()

    await db.refresh(job)
    assert job.status == "failed"
    state = await _updates(client, token)
    assert state["status"] == "human_requested"
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


# ---------------------------------------------------------------------------
# History window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_returns_the_latest_messages_of_a_long_conversation(
    db, client, org_scope, monkeypatch
):
    _stub(monkeypatch)
    org_id, token = await _setup(db, client, org_scope)
    session = (
        await db.execute(
            select(PublicChatSession).where(PublicChatSession.organization_id == org_id)
        )
    ).scalar_one()
    for n in range(60):
        db.add(
            ConversationMessage(
                organization_id=org_id,
                conversation_id=session.conversation_id,
                provider="cxops",
                dedupe_key=f"{DEDUPE_INBOUND_PREFIX}bulk-{n}",
                direction="inbound",
                visibility="public",
                body=f"m{n}",
                sent_at=datetime.now(UTC) + timedelta(seconds=n),
            )
        )
    await db.commit()

    response = await client.get("/public/chat/sessions/state", headers=base._origin_headers(token))
    bodies = [m["body"] for m in response.json()["messages"]]
    assert len(bodies) == 50
    assert bodies[-1] == "m59", "the newest message must be visible"
    assert bodies[0] == "m10"


# ---------------------------------------------------------------------------
# Staff usage and service-health report
# ---------------------------------------------------------------------------


def _staff_headers(subject: str, organization_id: int) -> dict:
    """A staff JWT built from the auth settings the autouse fixture pinned."""
    import os

    from jose import jwt

    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {
            "sub": subject,
            "email": f"{subject}@example.com",
            "iss": os.environ["AUTH_JWT_ISSUER"],
            "aud": os.environ["AUTH_JWT_AUDIENCE"],
            "exp": now + 3600,
            "iat": now,
        },
        os.environ["AUTH_JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}", "x-cxops-organization-id": str(organization_id)}


async def _add_owner(db, subject: str, organization_id: int) -> None:
    from app.core.rbac import OrganizationRole
    from app.models.organization_membership import OrganizationMembership

    db.add(
        OrganizationMembership(
            subject=subject, organization_id=organization_id, role=OrganizationRole.OWNER
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_usage_report_is_tenant_scoped(db, client, org_scope, monkeypatch):
    _stub(monkeypatch)
    org_a, token_a = await _setup(db, client, org_scope)
    org_b, _token_b = await _setup(db, client, org_scope)
    await _send(client, token_a, "one")
    await _send(client, token_a, "two")
    await turn_dispatcher.drain()
    subject_a = f"usage-a-{uuid.uuid4().hex[:8]}"
    subject_b = f"usage-b-{uuid.uuid4().hex[:8]}"
    await _add_owner(db, subject_a, org_a)
    await _add_owner(db, subject_b, org_b)

    response = await client.get("/observability/usage", headers=_staff_headers(subject_a, org_a))
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["chat_turns_24h"]["completed"] == 2
    assert report["chat_turns_24h"]["open"] == 0
    assert report["chat_turns_24h"]["p50_seconds"] is not None
    assert report["allowance"]["turns_used"] == 2
    assert report["allowance"]["allowed"] is True

    cross = await client.get("/observability/usage", headers=_staff_headers(subject_b, org_a))
    assert cross.status_code in (403, 404)
    anonymous = await client.get("/observability/usage")
    assert anonymous.status_code in (401, 403)
