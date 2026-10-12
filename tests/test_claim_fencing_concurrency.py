"""Phase 1P.8R — deterministic two-session races on job and message state.

Each test forces ONE precise interleaving with explicit ``asyncio.Event``
barriers (never sleeps) between two real PostgreSQL sessions:

  worker A reads  ->  worker B commits a newer claim / outcome  ->  worker A writes

and asserts that A's late write cannot overwrite B's state. The barriers hook
the read A performs immediately before its write, so the same test exercises a
read-check-write implementation (vulnerable) and a database-enforced
compare-and-update (safe) without changing.

Fake Zendesk only; any real network call fails (``_no_network``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.core.database import AsyncSessionLocal
from app.integrations.zendesk.client import ZendeskClient
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.integration_job_repository import IntegrationJobRepository
from app.services.conversation_reply_service import ConversationReplyService
from app.services.integration_job_service import IntegrationJobService
from scripts import worker
from tests import test_job_fault_injection as harness

# Shared fixtures and helpers from the fault-injection harness (fake Zendesk,
# network guard, tenant scope). Assigned, not imported, so pytest registers the
# fixtures in this module.
_no_network = harness._no_network
org_scope = harness.org_scope
zendesk = harness.zendesk
_claim_specific = harness._claim_specific
_fresh = harness._fresh
_pending_jobs = harness._pending_jobs
_zendesk_reply = harness._zendesk_reply

BARRIER_TIMEOUT = 10


class Interleave:
    """A reads -> B acts and commits -> A continues."""

    def __init__(self) -> None:
        self.a_has_read = asyncio.Event()
        self.b_committed = asyncio.Event()
        self.armed = True

    async def pause_a(self) -> None:
        if not self.armed:
            return
        self.armed = False
        self.a_has_read.set()
        await asyncio.wait_for(self.b_committed.wait(), BARRIER_TIMEOUT)

    async def run_b(self, action) -> None:
        await asyncio.wait_for(self.a_has_read.wait(), BARRIER_TIMEOUT)
        try:
            await action()
        finally:
            self.b_committed.set()


def _hook_after(monkeypatch, owner, name, interleave: Interleave, *, call: int = 1):
    """Pause A right after its ``call``-th invocation of ``owner.name``."""
    original = getattr(owner, name)
    seen = {"n": 0}

    async def _hooked(*args, **kwargs):
        result = await original(*args, **kwargs)
        seen["n"] += 1
        if seen["n"] == call:
            await interleave.pause_a()
        return result

    monkeypatch.setattr(owner, name, staticmethod(_hooked))
    return seen


def _hook_before_first(monkeypatch, targets, interleave: Interleave) -> None:
    """Pause A right BEFORE the first call to any of ``targets`` - the start of
    its write path, whichever implementation is in place (read-check-write
    reads first; the fenced implementation writes first)."""
    for owner, name in targets:
        if not hasattr(owner, name):
            continue
        original = getattr(owner, name)

        async def _hooked(*args, _original=original, **kwargs):
            await interleave.pause_a()
            return await _original(*args, **kwargs)

        monkeypatch.setattr(owner, name, staticmethod(_hooked))


async def _reclaim(job_id: int, *, new_claim: datetime, attempts: int) -> None:
    """Worker B: stale recovery + re-claim, committed."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(status="processing", locked_at=new_claim, attempts=attempts)
        )
        await session.commit()


async def _claim_row(job_id: int, claimed_at: datetime, attempts: int = 1) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(status="processing", locked_at=claimed_at, attempts=attempts)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Terminal job writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["mark_failed", "mark_completed"])
@pytest.mark.asyncio
async def test_reclaim_between_read_and_write_cannot_be_overwritten(
    org_scope, monkeypatch, operation
):
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(org_id, 1)
    a_claim = datetime.now(UTC) - timedelta(hours=1)
    b_claim = datetime.now(UTC)
    await _claim_row(job_id, a_claim)

    gate = Interleave()
    _hook_after(monkeypatch, IntegrationJobRepository, "get_by_id_unscoped", gate)

    async def worker_a():
        async with AsyncSessionLocal() as session:
            if operation == "mark_failed":
                return await IntegrationJobRepository.mark_failed(
                    session, job_id=job_id, error_message="late", claimed_at=a_claim
                )
            return await IntegrationJobRepository.mark_completed(
                session, job_id=job_id, claimed_at=a_claim
            )

    result, _ = await asyncio.gather(
        worker_a(),
        gate.run_b(lambda: _reclaim(job_id, new_claim=b_claim, attempts=2)),
    )

    assert result is None, "a lost claim must be reported, not applied"
    job = await _fresh(IntegrationJob, job_id)
    assert job.status == "processing"
    assert job.locked_at == b_claim
    assert job.attempts == 2


@pytest.mark.asyncio
async def test_current_claim_still_completes_and_fails_normally(org_scope):
    org_id = await org_scope()
    done_id, failed_id = await _pending_jobs(org_id, 2)
    claim = datetime.now(UTC)
    await _claim_row(done_id, claim)
    await _claim_row(failed_id, claim, attempts=5)

    async with AsyncSessionLocal() as session:
        completed = await IntegrationJobRepository.mark_completed(
            session, job_id=done_id, claimed_at=claim
        )
        failed = await IntegrationJobRepository.mark_failed(
            session, job_id=failed_id, error_message="x", claimed_at=claim
        )

    assert completed is not None and completed.status == "completed"
    assert completed.locked_at is None and completed.completed_at is not None
    assert failed is not None and failed.status == "failed"


@pytest.mark.asyncio
async def test_retry_backoff_is_applied_atomically(org_scope):
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(org_id, 1)
    claim = datetime.now(UTC)
    await _claim_row(job_id, claim, attempts=3)
    before = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        retried = await IntegrationJobRepository.mark_failed(
            session, job_id=job_id, error_message="transient", claimed_at=claim
        )

    assert retried is not None and retried.status == "retry"
    assert retried.locked_at is None
    assert retried.available_at >= before + timedelta(seconds=7)  # 2**3 = 8 s
    assert retried.last_error == "transient"


# ---------------------------------------------------------------------------
# Non-retryable delivery failure branch vs a newer owner that delivered
# ---------------------------------------------------------------------------


async def _newer_worker_delivered(job_id: int, message_id: int, b_claim: datetime) -> None:
    """Worker B reclaimed the job, delivered the reply, and completed the job."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ConversationMessage)
            .where(ConversationMessage.id == message_id)
            .values(delivery_status="sent", delivered_at=datetime.now(UTC))
        )
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(status="completed", locked_at=None, completed_at=datetime.now(UTC))
        )
        await session.commit()
    del b_claim


async def _newer_worker_still_running(job_id: int, message_id: int, b_claim: datetime) -> None:
    """Worker B reclaimed the job and is mid-delivery (message 'sending')."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ConversationMessage)
            .where(ConversationMessage.id == message_id)
            .values(delivery_status="sending")
        )
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(status="processing", locked_at=b_claim, attempts=2)
        )
        await session.commit()


@pytest.mark.parametrize(
    ("newer_worker", "expected_job", "expected_message"),
    [
        (_newer_worker_delivered, "completed", "sent"),
        (_newer_worker_still_running, "processing", "sending"),
    ],
    ids=["newer-worker-delivered", "newer-worker-running"],
)
@pytest.mark.asyncio
async def test_lost_claim_cannot_overwrite_newer_delivery_state(
    db, org_scope, zendesk, monkeypatch, newer_worker, expected_job, expected_message
):
    """A hits a NON-retryable delivery error after losing its claim. Its
    failure branch must not mark B's job failed nor B's message failed."""
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id, external_id="not-a-number")
    job = await _claim_specific(db, case["job_id"])
    b_claim = datetime.now(UTC) + timedelta(seconds=1)

    gate = Interleave()
    # B wins the race: it commits before A's failure branch writes anything.
    _hook_before_first(
        monkeypatch,
        [
            (IntegrationJobRepository, "get_by_id_unscoped"),
            (IntegrationJobRepository, "fail_claimed"),
        ],
        gate,
    )

    async def worker_a():
        return await worker.process_claimed_job(db, job)

    result, _ = await asyncio.gather(
        worker_a(),
        gate.run_b(lambda: newer_worker(case["job_id"], case["message_id"], b_claim)),
    )

    persisted_job = await _fresh(IntegrationJob, case["job_id"])
    message = await _fresh(ConversationMessage, case["message_id"])
    assert persisted_job.status == expected_job
    assert message.delivery_status == expected_message
    assert result.outcome == "claim_lost"
    assert zendesk.sends == 0


@pytest.mark.asyncio
async def test_failure_sync_cannot_overwrite_a_reply_sent_meanwhile(db, org_scope, monkeypatch):
    """handle_job_failure reads the message, another worker marks it sent, then
    the stale read must not turn it back into 'retrying'."""
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    gate = Interleave()
    _hook_after(monkeypatch, ConversationMessageRepository, "get_by_id_for_tenant", gate)

    async def mark_sent():
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ConversationMessage)
                .where(ConversationMessage.id == case["message_id"])
                .values(delivery_status="sent", delivered_at=datetime.now(UTC))
            )
            await session.commit()

    async def worker_a():
        async with AsyncSessionLocal() as session:
            await ConversationReplyService.handle_job_failure(
                session,
                message_id=case["message_id"],
                organization_id=org_id,
                error_code="provider_unavailable",
                will_retry=True,
            )

    await asyncio.gather(worker_a(), gate.run_b(mark_sent))

    message = await _fresh(ConversationMessage, case["message_id"])
    assert message.delivery_status == "sent"
    assert message.delivery_error_code is None


# ---------------------------------------------------------------------------
# Two workers delivering the same reply concurrently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_delivery_of_one_reply_sends_once(org_scope, zendesk, monkeypatch):
    """A is inside the provider call when B (holding a newer claim after a
    stale recovery) starts delivering the same message. B must not send."""
    org_id = await org_scope()
    async with AsyncSessionLocal() as setup:
        case = await _zendesk_reply(setup, org_id)

    a_in_provider = asyncio.Event()
    b_finished = asyncio.Event()
    real_apply = ZendeskClient.apply_agent_action

    async def _slow_apply(self, *args, **kwargs):
        if not a_in_provider.is_set():
            a_in_provider.set()
            await asyncio.wait_for(b_finished.wait(), BARRIER_TIMEOUT)
        return await real_apply(self, *args, **kwargs)

    monkeypatch.setattr(ZendeskClient, "apply_agent_action", _slow_apply)

    async def deliver(session):
        job = await IntegrationJobRepository.get_by_id_unscoped(session, case["job_id"])
        assert job is not None
        try:
            await IntegrationJobService.execute(db=session, job=job)
            await session.commit()
            return "delivered"
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return type(exc).__name__

    async def worker_a():
        async with AsyncSessionLocal() as session:
            return await deliver(session)

    async def worker_b():
        await asyncio.wait_for(a_in_provider.wait(), BARRIER_TIMEOUT)
        try:
            async with AsyncSessionLocal() as session:
                return await deliver(session)
        finally:
            b_finished.set()

    a_result, b_result = await asyncio.gather(worker_a(), worker_b())

    assert zendesk.sends == 1, f"duplicate customer reply (A={a_result}, B={b_result})"
    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1
    assert a_result == "delivered"
    assert b_result == "ConversationDeliveryTransientError"
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"


@pytest.mark.asyncio
async def test_cancelled_worker_releases_the_delivery_lease(org_scope, zendesk, monkeypatch):
    """Cancellation (e.g. shutdown) mid provider call: nothing is committed, the
    job keeps A's claim for stale recovery, and the per-message lease is freed
    so the recovered run can deliver exactly once."""
    org_id = await org_scope()
    async with AsyncSessionLocal() as setup:
        case = await _zendesk_reply(setup, org_id)
        await _claim_specific(setup, case["job_id"])
    entered = asyncio.Event()
    real_apply = ZendeskClient.apply_agent_action

    async def _hang(self, *args, **kwargs):
        entered.set()
        await asyncio.Event().wait()  # never returns; cancelled below

    monkeypatch.setattr(ZendeskClient, "apply_agent_action", _hang)

    async def worker_a():
        async with AsyncSessionLocal() as session:
            job = await IntegrationJobRepository.get_by_id_unscoped(session, case["job_id"])
            assert job is not None
            await worker.process_claimed_job(session, job)

    task = asyncio.create_task(worker_a())
    await asyncio.wait_for(entered.wait(), BARRIER_TIMEOUT)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = await _fresh(IntegrationJob, case["job_id"])
    message = await _fresh(ConversationMessage, case["message_id"])
    assert job.status == "processing", "claim stays for stale recovery"
    assert message.delivery_status == "queued", "nothing from the cancelled run was committed"

    # Recovered run: the lease is free and delivery happens exactly once.
    monkeypatch.setattr(ZendeskClient, "apply_agent_action", real_apply)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == case["job_id"])
            .values(status="retry", locked_at=None)
        )
        await session.commit()
        recovered = await _claim_specific(session, case["job_id"])
        result = await worker.process_claimed_job(session, recovered)

    assert result.outcome == "completed"
    assert zendesk.sends == 1
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"
    rows = None
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(select(IntegrationJob.status).where(IntegrationJob.id == case["job_id"]))
        ).scalar_one()
    assert rows == "completed"


@harness.requires_disposable_db
@pytest.mark.asyncio
async def test_stale_recovery_end_to_end_with_real_claims(org_scope, monkeypatch):
    """A claims; A's claim goes stale; B's real claim recovers and re-claims;
    A's late completion is rejected; B completes. Attempts never exceed max."""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(IntegrationJob))
        await session.commit()
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(org_id, 1)

    async with AsyncSessionLocal() as a_session:
        a_job = await IntegrationJobRepository.claim_next_unscoped(a_session)
        assert a_job is not None and a_job.id == job_id
        a_claim = a_job.locked_at

    async with AsyncSessionLocal() as session:  # A ran past the stale window
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(locked_at=a_claim - timedelta(hours=1))
        )
        await session.commit()
    a_claim = a_claim - timedelta(hours=1)

    async with AsyncSessionLocal() as b_session:
        b_job = await IntegrationJobRepository.claim_next_unscoped(b_session)
        assert b_job is not None and b_job.id == job_id and b_job.attempts == 2
        b_claim = b_job.locked_at

    async with AsyncSessionLocal() as a_session:
        assert (
            await IntegrationJobRepository.mark_completed(
                a_session, job_id=job_id, claimed_at=a_claim
            )
            is None
        )
    async with AsyncSessionLocal() as b_session:
        done = await IntegrationJobRepository.mark_completed(
            b_session, job_id=job_id, claimed_at=b_claim
        )
    assert done is not None and done.status == "completed" and done.attempts == 2
