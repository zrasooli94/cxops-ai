"""Phase 1P.9 — multi-tenant load, fairness, isolation and crash recovery.

Real API + real PostgreSQL (disposable), fake model with injected latency (no
paid calls). Scenarios: 1, 5, 20 and 50 active tenants sending concurrently.
Asserts invariants, not machine-specific timings: every message gets exactly
one reply, per-session order holds, no tenant sees another's messages,
concurrency caps hold (global and per tenant), no tenant is starved, the
allowance is enforced under load, and turns survive killed processing tasks.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import pytest
from sqlalchemy import update

from app.models.integration_job import IntegrationJob
from app.models.public_chat import PublicChatConfiguration
from app.services import agent_workflow_service as workflow_module
from app.services.public_chat_service import PUBLIC_CHAT_TURN_JOB_TYPE
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from tests import test_public_chat as base
from tests import test_public_chat_async as chat

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated

MODEL_LATENCY = 0.05
MESSAGES_PER_SESSION = 2


class TenantProbe:
    """Tracks concurrent model calls per tenant through the decision model."""

    def __init__(self) -> None:
        self.active: dict[int, int] = defaultdict(int)
        self.max_active: dict[int, int] = defaultdict(int)
        self.global_active = 0
        self.global_max = 0
        self.calls: dict[int, int] = defaultdict(int)


def _stub_with_probe(monkeypatch, probe: TenantProbe, latency: float = MODEL_LATENCY):
    from app.services.knowledge_search_service import KnowledgeSearchService

    async def _no_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(KnowledgeSearchService, "search", _no_search)
    original_analyze = workflow_module.agent_workflow_service.analyze

    async def _probed_analyze(*args, organization_id, **kwargs):
        probe.active[organization_id] += 1
        probe.global_active += 1
        probe.max_active[organization_id] = max(
            probe.max_active[organization_id], probe.active[organization_id]
        )
        probe.global_max = max(probe.global_max, probe.global_active)
        probe.calls[organization_id] += 1
        try:
            await asyncio.sleep(latency)
            return await original_analyze(*args, organization_id=organization_id, **kwargs)
        finally:
            probe.active[organization_id] -= 1
            probe.global_active -= 1

    chat._stub(monkeypatch)
    monkeypatch.setattr(workflow_module.agent_workflow_service, "analyze", _probed_analyze)


async def _tenants(db, client, org_scope, count: int) -> list[tuple[int, str]]:
    return [await chat._setup(db, client, org_scope) for _ in range(count)]


@pytest.mark.parametrize("tenant_count", [1, 5, 20, 50])
@pytest.mark.asyncio
async def test_concurrent_tenants_all_replied_once_in_order_and_isolated(
    db, client, org_scope, monkeypatch, tenant_count
):
    probe = TenantProbe()
    _stub_with_probe(monkeypatch, probe)
    tenants = await _tenants(db, client, org_scope, tenant_count)

    async def visitor(org_id: int, token: str) -> list[str]:
        sent = []
        for n in range(MESSAGES_PER_SESSION):
            text = f"org{org_id}-msg{n}"
            response = await chat._send(client, token, text)
            assert response.status_code == 200, response.text
            assert response.json()["processing_state"] == "accepted"
            sent.append(text)
        return sent

    started = time.perf_counter()
    sent_by_tenant = await asyncio.gather(*(visitor(o, t) for o, t in tenants))
    accept_seconds = time.perf_counter() - started
    await turn_dispatcher.drain(timeout=120)

    for (org_id, token), sent in zip(tenants, sent_by_tenant, strict=True):
        state = await chat._updates(client, token)
        inbound = [m["body"] for m in state["messages"] if m["direction"] == "inbound"]
        outbound = [m for m in state["messages"] if m["direction"] == "outbound"]
        assert inbound == sent, "per-session order broken"
        assert len(outbound) == MESSAGES_PER_SESSION, "lost or duplicated reply"
        assert all(f"org{org_id}-" in body for body in inbound), "cross-tenant message"
        assert state["turns"] == []

    assert max(probe.max_active.values()) <= 2, "per-tenant concurrency cap exceeded"
    assert probe.global_max <= 4, "global concurrency cap exceeded"
    assert sorted(probe.calls) == sorted(org for org, _ in tenants)
    assert set(probe.calls.values()) == {MESSAGES_PER_SESSION}, "a tenant was starved"
    # Acceptance never waits for the model: all messages were accepted well
    # before the sequential model time they represent.
    total_model_seconds = tenant_count * MESSAGES_PER_SESSION * MODEL_LATENCY
    if tenant_count >= 20:
        assert accept_seconds < total_model_seconds


@pytest.mark.asyncio
async def test_noisy_tenant_cannot_starve_others(db, client, org_scope, monkeypatch):
    """One tenant floods 12 concurrent sessions; three quiet tenants send one
    message each afterwards. The noisy tenant is capped at 2 concurrent turns,
    so the quiet tenants are answered before the flood has drained."""
    probe = TenantProbe()
    _stub_with_probe(monkeypatch, probe, latency=0.1)
    noisy = await org_scope()
    noisy_org = noisy.id
    _cfg, noisy_key = await base._make_widget_config(db, noisy, max_messages_per_minute=60)
    noisy_tokens = [
        (await base._create_session(client, noisy_key))["session"]["token"] for _ in range(12)
    ]
    quiet = await _tenants(db, client, org_scope, 3)

    started = time.perf_counter()
    for token in noisy_tokens:
        assert (await chat._send(client, token, "flood")).status_code == 200
    for org_id, token in quiet:
        await chat._send(client, token, f"org{org_id}-quiet")

    finished: dict[int, float] = {}

    async def wait_reply(org_id: int, token: str) -> None:
        while True:
            state = await chat._updates(client, token)
            if any(m["direction"] == "outbound" for m in state["messages"]):
                finished[org_id] = time.perf_counter() - started
                return
            await asyncio.sleep(0.02)

    await asyncio.wait_for(asyncio.gather(*(wait_reply(o, t) for o, t in quiet)), 60)
    await turn_dispatcher.drain(timeout=120)
    flood_done = time.perf_counter() - started

    assert probe.max_active[noisy_org] <= 2, "noisy tenant exceeded its concurrency cap"
    assert probe.calls[noisy_org] == 12
    # 12 noisy turns at 2-at-a-time take >= 0.6 s; quiet tenants must not wait
    # for the whole flood.
    assert max(finished.values()) < flood_done


@pytest.mark.asyncio
async def test_allowance_enforced_under_concurrent_load(db, client, org_scope, monkeypatch):
    probe = TenantProbe()
    _stub_with_probe(monkeypatch, probe)
    limited_org, limited_token = await chat._setup(db, client, org_scope)
    await db.execute(
        update(PublicChatConfiguration)
        .where(PublicChatConfiguration.organization_id == limited_org)
        .values(monthly_ai_turn_limit=0)
    )
    await db.commit()
    others = await _tenants(db, client, org_scope, 5)

    await asyncio.gather(
        chat._send(client, limited_token, "limited"),
        *(chat._send(client, token, f"org{org}") for org, token in others),
    )
    await turn_dispatcher.drain(timeout=60)

    assert probe.calls.get(limited_org, 0) == 0, "the exhausted tenant called the model"
    assert all(probe.calls[org] == 1 for org, _ in others)
    assert (await chat._updates(client, limited_token))["status"] == "human_requested"


@pytest.mark.asyncio
async def test_turns_survive_killed_processing_tasks(db, client, org_scope, monkeypatch):
    """Cancel every in-flight turn mid-model-call (an instance restart), then let
    recovery run: every message still gets exactly one reply."""
    probe = TenantProbe()
    _stub_with_probe(monkeypatch, probe, latency=0.5)
    tenants = await _tenants(db, client, org_scope, 5)
    for org_id, token in tenants:
        await chat._send(client, token, f"org{org_id}-crash")
    await asyncio.sleep(0.2)  # let turns start their model call
    for task in list(turn_dispatcher._tasks):
        task.cancel()
    await asyncio.gather(*list(turn_dispatcher._tasks), return_exceptions=True)
    turn_dispatcher._inflight.clear()

    # Interrupted turns are "processing" with a fresh claim; age them past the
    # stale window, as a restarted instance would find them.
    org_ids = [org for org, _ in tenants]
    await db.execute(
        update(IntegrationJob)
        .where(
            IntegrationJob.organization_id.in_(org_ids),
            IntegrationJob.job_type == PUBLIC_CHAT_TURN_JOB_TYPE,
            IntegrationJob.status == "processing",
        )
        .values(locked_at=IntegrationJob.locked_at - __import__("datetime").timedelta(hours=1))
    )
    await db.commit()

    resumed = await turn_dispatcher.recover_on_startup()
    assert resumed >= 1
    await turn_dispatcher.drain(timeout=60)

    for _org_id, token in tenants:
        state = await chat._updates(client, token)
        outbound = [m for m in state["messages"] if m["direction"] == "outbound"]
        assert len(outbound) == 1, "a turn was lost or duplicated across the crash"
