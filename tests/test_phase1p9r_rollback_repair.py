"""Phase 1P.9R — recovering turns stranded by a code rollback.

Verified against master b50e741: the pre-1P.9 worker fails a queued
``public_chat.turn`` permanently ("Unknown job type") without replying or
handing off. After rolling forward, ``scripts.repair_rolled_back_chat_turns``
re-queues exactly those turns (dry-run by default, idempotent, never touching
answered, handed-off or unrelated jobs), and recovery answers them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.models.integration_job import IntegrationJob
from app.models.public_chat import PublicChatSession
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from scripts.repair_rolled_back_chat_turns import ROLLBACK_ERROR_PREFIX, repair
from tests import test_public_chat as base
from tests import test_public_chat_async as chat

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated
_async_platform_on = chat._async_platform_on


async def _rolled_back_turn(db, client, org_scope, monkeypatch, *, answered=False):
    """A turn the new release accepted and the OLD worker then failed."""
    org_id, token = await chat._setup(db, client, org_scope)
    if answered:
        await chat._send(client, token, "answered before the rollback")
        await turn_dispatcher.drain()
    else:
        with monkeypatch.context() as scoped:
            scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
            await chat._send(client, token, "queued at rollback time")
    await db.execute(
        update(IntegrationJob)
        .where(IntegrationJob.organization_id == org_id)
        .values(status="failed", attempts=1, last_error=f"{ROLLBACK_ERROR_PREFIX} (old worker)")
    )
    await db.commit()
    return org_id, token


@pytest.mark.asyncio
async def test_rollback_stranded_turn_is_requeued_and_answered(db, client, org_scope, monkeypatch):
    chat._stub(monkeypatch)
    stranded_org, stranded_token = await _rolled_back_turn(db, client, org_scope, monkeypatch)
    answered_org, _t = await _rolled_back_turn(db, client, org_scope, monkeypatch, answered=True)
    handed_org, _t2 = await _rolled_back_turn(db, client, org_scope, monkeypatch)
    await db.execute(
        update(PublicChatSession)
        .where(PublicChatSession.organization_id == handed_org)
        .values(status="human_requested")
    )
    await db.commit()

    dry = await repair(db, apply=False, organization_id=stranded_org)
    assert len(dry.requeued) == 1
    (job,) = await chat._jobs(db, stranded_org)
    await db.refresh(job)
    assert job.status == "failed"  # dry run wrote nothing

    for org in (stranded_org, answered_org, handed_org):
        report = await repair(db, apply=True, organization_id=org)
        if org == stranded_org:
            assert len(report.requeued) == 1
        elif org == answered_org:
            assert report.already_replied and not report.requeued
        else:
            assert report.session_not_open and not report.requeued
    again = await repair(db, apply=True, organization_id=stranded_org)
    assert again.examined == 0  # idempotent

    await turn_dispatcher.recover_on_startup()
    await turn_dispatcher.drain()
    state = await chat._updates(client, stranded_token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]
    for org in (answered_org, handed_org):
        (other,) = await chat._jobs(db, org)
        await db.refresh(other)
        assert other.status == "failed"


@pytest.mark.asyncio
async def test_unrelated_failures_are_never_touched(db, client, org_scope, monkeypatch):
    chat._stub(monkeypatch)
    org_id, _token = await _rolled_back_turn(db, client, org_scope, monkeypatch)
    await db.execute(
        update(IntegrationJob)
        .where(IntegrationJob.organization_id == org_id)
        .values(last_error="PermanentJobError")
    )
    await db.commit()
    report = await repair(db, apply=True, organization_id=org_id)
    assert report.examined == 0
