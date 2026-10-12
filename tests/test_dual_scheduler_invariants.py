"""Phase 1P.9 readiness: two schedulers running the one-shot worker at once.

During a GitHub Actions + Render Cron Job trial both may start a run in the
same minute. Render's "one active run per cron job" guarantee says nothing
about a separate GitHub runner, so safety must come from the data layer:
SKIP LOCKED claims, fenced terminal writes, the per-reply delivery lease, and
complete marker verification. This drives two full ``run_worker_once`` passes
concurrently against one queue (disposable database only).
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from scripts import worker
from tests import test_job_fault_injection as harness

_no_network = harness._no_network
org_scope = harness.org_scope
zendesk = harness.zendesk


@harness.requires_disposable_db
@pytest.mark.parametrize("replies", [1, 6])
@pytest.mark.asyncio
async def test_two_concurrent_one_shot_runs_never_duplicate_effects(
    org_scope, zendesk, monkeypatch, replies
):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(IntegrationJob))
        await session.commit()
    org_id = await org_scope()
    cases = []
    async with AsyncSessionLocal() as setup:
        for n in range(replies):
            cases.append(
                await harness._zendesk_reply(setup, org_id, external_id=str(7000 + n))
            )

    # Widen the race: every provider write yields to the other run first.
    from app.integrations.zendesk.client import ZendeskClient

    real_apply = ZendeskClient.apply_agent_action

    async def _yielding_apply(self, *args, **kwargs):
        await asyncio.sleep(0)
        return await real_apply(self, *args, **kwargs)

    monkeypatch.setattr(ZendeskClient, "apply_agent_action", _yielding_apply)

    github, render = await asyncio.gather(
        worker.run_worker_once(max_jobs=25, max_seconds=240, finalize_expired_public_chat=True),
        worker.run_worker_once(max_jobs=25, max_seconds=240, finalize_expired_public_chat=True),
    )

    assert zendesk.sends == replies, "a customer reply was sent more than once"
    for case in cases:
        assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1
        message = await harness._fresh(ConversationMessage, case["message_id"])
        assert message.delivery_status == "sent"
    async with AsyncSessionLocal() as session:
        statuses = Counter(
            (await session.execute(select(IntegrationJob.status))).scalars().all()
        )
    assert statuses == Counter({"completed": replies})
    assert github["jobs_processed"] + render["jobs_processed"] == replies
    assert {github["health"], render["health"]} <= {"healthy"}
    # The expiry pass ran in both and stayed compatible with the summaries.
    assert "expiry_outcome" in github and "expiry_outcome" in render
