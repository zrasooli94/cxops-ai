"""Phase 1P.9R — transactionally safe message acceptance.

"accepted" must always mean "durably recoverable": the inbound message and
its turn job commit together. Covers a crash between the two inserts (the
Phase 1P.9 defect: the message committed, the job never existed), a failure
after the commit, lost responses with client retries, concurrent duplicate
submissions, and a session closed while its turn is queued.

Real API + disposable PostgreSQL, fake model (no provider calls).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.models.conversation_message import ConversationMessage
from app.models.ticket import Ticket
from app.repositories import savepoint as savepoint_module
from app.services import public_chat_service
from app.services.public_chat_service import DEDUPE_INBOUND_PREFIX
from app.services.public_chat_turn_dispatcher import turn_dispatcher
from tests import test_public_chat as base
from tests import test_public_chat_async as chat

_configure_auth = base._configure_auth
client = base.client
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated
_async_platform_on = chat._async_platform_on


class SimulatedCrash(RuntimeError):
    """The process dies at this point (no further statement reaches the DB)."""


async def _inbound_rows(db, org_id: int, client_message_id: str) -> list[ConversationMessage]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.organization_id == org_id,
                    ConversationMessage.dedupe_key
                    == f"{DEDUPE_INBOUND_PREFIX}{client_message_id}",
                )
            )
        ).scalars()
    )


def _crash_on_job_insert(monkeypatch) -> None:
    """Crash exactly when the turn job is about to be written (after the
    inbound message has been written in the same request)."""
    original = savepoint_module.insert_in_savepoint

    async def _crashing(db, *instances):
        from app.models.integration_job import IntegrationJob

        if any(isinstance(i, IntegrationJob) for i in instances):
            raise SimulatedCrash("process died before the job was stored")
        return await original(db, *instances)

    monkeypatch.setattr(public_chat_service, "insert_in_savepoint", _crashing)


@pytest.mark.asyncio
async def test_crash_between_message_and_job_leaves_no_orphaned_message(
    db, client, org_scope, monkeypatch
):
    """Fails on Phase 1P.9 (the message was already committed, with no job:
    a customer message nobody would ever answer). Passes now: nothing was
    stored, and the client's retry with the same id starts clean."""
    chat._stub(monkeypatch)
    org_id, token = await chat._setup(db, client, org_scope)

    with monkeypatch.context() as scoped:
        _crash_on_job_insert(scoped)
        with pytest.raises(SimulatedCrash):
            await chat._send(client, token, "Where is my order?", client_message_id="m-crash")

    assert await _inbound_rows(db, org_id, "m-crash") == []
    assert await chat._jobs(db, org_id) == []

    retry = await chat._send(client, token, "Where is my order?", client_message_id="m-crash")
    assert retry.json()["processing_state"] == "accepted"
    await turn_dispatcher.drain()
    assert len(await _inbound_rows(db, org_id, "m-crash")) == 1
    assert len(await chat._jobs(db, org_id)) == 1
    state = await chat._updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


@pytest.mark.asyncio
async def test_every_accepted_message_has_a_recoverable_job(db, client, org_scope, monkeypatch):
    chat._stub(monkeypatch)
    org_id, token = await chat._setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        # The in-process dispatch is lost (the process dies right after
        # responding): only the durable job can bring the turn back.
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        accepted = [
            (await chat._send(client, token, f"message {n}", client_message_id=f"m-{n}")).json()
            for n in range(3)
        ]
    assert all(r["processing_state"] == "accepted" for r in accepted)
    jobs = await chat._jobs(db, org_id)
    assert sorted(job.payload["message_id"] for job in jobs) == sorted(
        r["message_id"] for r in accepted
    )
    assert all(job.status == "pending" for job in jobs)

    await turn_dispatcher.recover_on_startup()
    await turn_dispatcher.drain()
    state = await chat._updates(client, token)
    assert [m["direction"] for m in state["messages"]].count("outbound") == 3


@pytest.mark.asyncio
async def test_ticket_mirror_failure_after_commit_still_accepts_and_answers(
    db, client, org_scope, monkeypatch
):
    chat._stub(monkeypatch)
    org_id, token = await chat._setup(db, client, org_scope)

    async def _mirror_down(*_a, **_k):
        raise SimulatedCrash("ticket update failed after the commit")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            public_chat_service.PublicChatService, "_record_inbound_on_ticket", _mirror_down
        )
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        response = await chat._send(client, token, "My car will not start")
    assert response.status_code == 200
    assert response.json()["processing_state"] == "accepted"

    await turn_dispatcher.recover_on_startup()
    await turn_dispatcher.drain()
    # The turn re-applied the mirror before analysis.
    db.expire_all()
    ticket = (
        await db.execute(select(Ticket).where(Ticket.organization_id == org_id))
    ).scalar_one()
    assert ticket.subject == "My car will not start"
    state = await chat._updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


@pytest.mark.asyncio
async def test_lost_response_retry_returns_the_same_message(db, client, org_scope, monkeypatch):
    """The client never saw the 'accepted' response (network drop) and
    retries with the same client_message_id - before and after the turn ran."""
    chat._stub(monkeypatch)
    org_id, token = await chat._setup(db, client, org_scope)
    first = (await chat._send(client, token, "Hi", client_message_id="m-net")).json()
    early_retry = (await chat._send(client, token, "Hi", client_message_id="m-net")).json()
    assert early_retry["message_id"] == first["message_id"]
    await turn_dispatcher.drain()
    late_retry = (await chat._send(client, token, "Hi", client_message_id="m-net")).json()
    assert late_retry["message_id"] == first["message_id"]
    assert late_retry["processing_state"] in {"completed", "handoff"}
    assert late_retry["reply"]
    assert len(await chat._jobs(db, org_id)) == 1
    state = await chat._updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


@pytest.mark.asyncio
async def test_concurrent_duplicate_submissions_converge(db, client, org_scope, monkeypatch):
    fake = chat._stub(monkeypatch, delay=0.05)
    org_id, token = await chat._setup(db, client, org_scope)
    responses = await asyncio.gather(
        *(chat._send(client, token, "Same message", client_message_id="m-dup") for _ in range(8))
    )
    # 429 is the burst limiter's documented over-limiting under concurrency
    # (a "retry later", never a lost message); 409 is "still in flight".
    assert all(r.status_code in (200, 409, 429) for r in responses), [
        r.text for r in responses
    ]
    await turn_dispatcher.drain()
    final = await chat._send(client, token, "Same message", client_message_id="m-dup")
    assert final.status_code == 200, final.text
    ok = [r.json() for r in responses if r.status_code == 200] + [final.json()]
    assert len({r["message_id"] for r in ok}) == 1
    assert len(await _inbound_rows(db, org_id, "m-dup")) == 1
    assert len(await chat._jobs(db, org_id)) == 1
    assert fake.invocation_count == 1
    state = await chat._updates(client, token)
    assert [m["direction"] for m in state["messages"]] == ["inbound", "outbound"]


@pytest.mark.asyncio
async def test_session_closed_while_queued_finishes_without_reply_or_error(
    db, client, org_scope, monkeypatch
):
    fake = chat._stub(monkeypatch)
    org_id, token = await chat._setup(db, client, org_scope)
    with monkeypatch.context() as scoped:
        scoped.setattr(turn_dispatcher, "submit", lambda *a, **k: False)
        await chat._send(client, token, "Never mind")
    closed = await client.post(
        "/public/chat/sessions/close", headers=base._origin_headers(token)
    )
    assert closed.status_code == 200
    await turn_dispatcher.recover_on_startup()
    await turn_dispatcher.drain()
    (job,) = await chat._jobs(db, org_id)
    await db.refresh(job)
    assert job.status == "completed"
    assert fake.invocation_count == 0
    # The customer's message is still stored for staff.
    db.expire_all()
    rows = (
        await db.execute(
            select(ConversationMessage).where(ConversationMessage.organization_id == org_id)
        )
    ).scalars().all()
    assert [m.direction for m in rows] == ["inbound"]
