"""Phase 1P.8 — deterministic fault injection for the durable job lifecycle.

Every scenario drives the REAL repositories, services, and worker job body
against a real PostgreSQL database, with a fake in-memory Zendesk. Any real
network call fails the test loudly (``_no_network`` patches the HTTP transport).

Scenario -> protected invariant (see docs/reviews for the full matrix):

  F01 invalid delivery target ........... non-retryable => terminal failed, no send
  F02 unsupported provider .............. non-retryable => terminal failed, no send
  F03 transient network failure ......... retry with backoff, message "retrying", no send
  F04 permanent provider rejection ...... terminal failed "provider_rejected", no retry loop
  F05 rollback inside a unit of work .... a duplicate collision never expires loaded
                                          objects or discards other pending work
  F06 commit failure after provider ok .. retry recovers by marker, exactly one send
  F07 interruption between stages ....... stale recovery re-runs; exactly one send
  F08 stale recovery bounds ............. live jobs not stolen; poison jobs stop at max
  F09 concurrent claims ................. each job claimed exactly once (SKIP LOCKED)
  F10 provider accepted, persistence lost  marker scan sees newest comments (>100)
  F11 repeated execution ................ already-sent reply => zero provider calls
  F12 cross-tenant mismatch ............. terminal failure, foreign row untouched
  F13 AI evaluation dependency failure .. bounded retry, sanitized error, then terminal
  F14 capacity / deadline exhaustion .... bounded claims; unclaimed jobs untouched
  F15 lost claim (fencing) .............. a stale worker cannot overwrite a new claim
  F16 marker scan failure ............... fail closed: no send without a successful scan

Tests that call the real GLOBAL claim (``claim_next_unscoped``) only run against
a disposable database: CI's ephemeral service DB (``CI=true``) or a database
whose name ends in ``_disposable_test``. Anywhere else they skip, so this suite
can never claim, recover, or alter a developer's existing jobs.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url

os.environ.setdefault("ENVIRONMENT", "development")

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.integrations.zendesk.client import ZendeskAPIError, ZendeskClient
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.ticket import Ticket
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.integration_job_repository import IntegrationJobRepository
from app.services.conversation_ingestion_service import ConversationIngestionService
from app.services.conversation_reply_service import ConversationReplyService
from scripts import worker

ORG_PREFIX = "fault-injection-"


def _disposable_database() -> bool:
    if os.environ.get("CI", "").lower() == "true":
        return True
    try:
        name = make_url(settings.database_url).database or ""
    except Exception:  # noqa: BLE001
        return False
    return name.endswith("_disposable_test")


requires_disposable_db = pytest.mark.skipif(
    not _disposable_database(),
    reason="real global claims only run against a disposable database",
)


# ---------------------------------------------------------------------------
# Network guard + fake Zendesk
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    async def _blocked(self, request, *args, **kwargs):
        raise AssertionError(f"unexpected real network call: {request.method} {request.url.host}")

    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked)


class FakeZendesk:
    """In-memory Zendesk ticket comments with the real API's paging shape.

    ``GET comments`` returns at most 100 comments, oldest first unless
    ``newest_first`` asks for ``sort_order=desc`` - exactly what the real List
    Comments endpoint does.
    """

    PAGE = 100

    def __init__(self) -> None:
        self.comments: dict[int, list[dict]] = {}
        self.sends = 0
        self.fail_scan_times = 0
        self.fail_send: Exception | None = None

    def seed(self, ticket_id: int, count: int) -> None:
        bucket = self.comments.setdefault(ticket_id, [])
        for _ in range(count):
            bucket.append({"id": f"c{len(bucket) + 1}", "body": "older comment"})

    def marker_count(self, ticket_id: int, token: str) -> int:
        return sum(token in c["body"] for c in self.comments.get(ticket_id, []))

    def install(self, monkeypatch) -> None:
        fake = self

        async def get_ticket_comments(self, db, ticket_id, *, organization_id, **kwargs):
            if fake.fail_scan_times > 0:
                fake.fail_scan_times -= 1
                raise ZendeskAPIError("Zendesk API error 503")
            bucket = list(fake.comments.get(int(ticket_id), []))
            if kwargs.get("newest_first"):
                bucket.reverse()
            return {"comments": bucket[: fake.PAGE]}

        async def apply_agent_action(self, db, ticket_id, *, organization_id, comment=None, **kwargs):
            if fake.fail_send is not None:
                raise fake.fail_send
            fake.sends += 1
            bucket = fake.comments.setdefault(int(ticket_id), [])
            bucket.append({"id": f"c{len(bucket) + 1}", "body": comment or ""})
            return {}

        monkeypatch.setattr(ZendeskClient, "get_ticket_comments", get_ticket_comments)
        monkeypatch.setattr(ZendeskClient, "apply_agent_action", apply_agent_action)


@pytest.fixture
def zendesk(monkeypatch) -> FakeZendesk:
    fake = FakeZendesk()
    fake.install(monkeypatch)
    return fake


# ---------------------------------------------------------------------------
# Tenant fixtures
# ---------------------------------------------------------------------------


_PURGE = [
    "ticket_events",
    "service_escalations",
    "integration_jobs",
    "conversation_messages",
    "conversations",
    "tickets",
    "customers",
]


@pytest_asyncio.fixture
async def org_scope(db):
    org_ids: list[int] = []

    async def make() -> int:
        org = Organization(name=f"{ORG_PREFIX}{uuid.uuid4().hex[:10]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        await db.commit()
        return org.id

    yield make

    await db.rollback()
    async with AsyncSessionLocal() as cleanup:
        for name in _PURGE:
            table = Base.metadata.tables.get(name)
            if table is not None and "organization_id" in table.columns:
                await cleanup.execute(delete(table).where(table.c.organization_id.in_(org_ids)))
        await cleanup.execute(delete(Organization).where(Organization.id.in_(org_ids)))
        await cleanup.commit()


async def _zendesk_reply(db, org_id: int, *, external_id: str = "4242", body: str = "Hello") -> dict:
    """A zendesk ticket + conversation + queued human reply job. Plain ids out."""
    ticket = Ticket(
        organization_id=org_id,
        subject="Zendesk case",
        description="Customer issue.",
        source="zendesk",
        external_id=external_id,
    )
    db.add(ticket)
    await db.commit()
    conversation = await ConversationIngestionService.sync_ticket_conversation(
        db, ticket=ticket, organization_id=org_id
    )
    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body=body,
        client_request_id=str(uuid.uuid4()),
        organization_id=org_id,
        requested_by_subject="agent-1",
    )
    message = await ConversationMessageRepository.get_by_id_for_tenant(
        db, message_id=result["message_id"], organization_id=org_id
    )
    assert message is not None
    return {
        "ticket_id": ticket.id,
        "zendesk_ticket_id": int(external_id) if external_id.isdigit() else None,
        "conversation_id": conversation.id,
        "message_id": result["message_id"],
        "job_id": result["job_id"],
        "token": message.delivery_token,
    }


async def _claim_specific(db, job_id: int) -> IntegrationJob:
    """Claim exactly this job the way ``claim_next_unscoped`` does (no global claim)."""
    job = await IntegrationJobRepository.get_by_id_unscoped(db, job_id)
    assert job is not None
    job.status = "processing"
    job.attempts += 1
    job.locked_at = datetime.now(UTC)
    await db.commit()
    return job


async def _fresh(model, row_id: int):
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(model).where(model.id == row_id))).scalar_one()


async def _run_job(db, job_id: int) -> None:
    job = await _claim_specific(db, job_id)
    await worker.process_claimed_job(db, job)


# ---------------------------------------------------------------------------
# F01 / F02 / F12 - non-retryable classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f01_invalid_target_is_terminal_without_send(db, org_scope, zendesk):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id, external_id="not-a-number")

    await _run_job(db, case["job_id"])

    job = await _fresh(IntegrationJob, case["job_id"])
    message = await _fresh(ConversationMessage, case["message_id"])
    assert job.status == "failed"
    assert job.attempts == 1
    assert message.delivery_status == "failed"
    assert message.delivery_error_code == "target_unavailable"
    assert zendesk.sends == 0


@pytest.mark.asyncio
async def test_f02_unsupported_provider_is_terminal_without_send(db, org_scope, zendesk):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(ConversationMessage)
            .where(ConversationMessage.id == case["message_id"])
            .values(provider="sms")
        )
        await other.execute(
            update(Conversation)
            .where(Conversation.id == case["conversation_id"])
            .values(provider="sms")
        )
        await other.commit()

    await _run_job(db, case["job_id"])

    job = await _fresh(IntegrationJob, case["job_id"])
    message = await _fresh(ConversationMessage, case["message_id"])
    assert job.status == "failed"
    assert message.delivery_status == "failed"
    assert zendesk.sends == 0


@pytest.mark.asyncio
async def test_f12_cross_tenant_payload_is_terminal_and_isolated(db, org_scope, zendesk):
    org_a = await org_scope()
    org_b = await org_scope()
    case = await _zendesk_reply(db, org_a)
    # A crafted job bound to tenant B pointing at tenant A's message.
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == case["job_id"])
            .values(organization_id=org_b)
        )
        await other.commit()

    await _run_job(db, case["job_id"])

    job = await _fresh(IntegrationJob, case["job_id"])
    message = await _fresh(ConversationMessage, case["message_id"])
    assert job.status == "failed", "a structurally invalid job must not burn retries"
    assert job.attempts == 1
    assert message.delivery_status == "queued"
    assert message.organization_id == org_a
    assert zendesk.sends == 0


@pytest.mark.asyncio
async def test_f01_message_deleted_before_failure_reload_does_not_crash(
    db, org_scope, zendesk, monkeypatch
):
    """PR #3 path: after the rollback the message is re-loaded tenant-scoped.
    If it vanished meanwhile, the job is still failed and nothing crashes."""
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id, external_id="not-a-number")
    original = ConversationMessageRepository.get_by_id_for_tenant
    calls = {"n": 0}

    async def _gone_on_reload(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 3:  # execute's lookup, deliver's lookup, then the reload
            return None
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        ConversationMessageRepository, "get_by_id_for_tenant", staticmethod(_gone_on_reload)
    )
    await _run_job(db, case["job_id"])

    assert calls["n"] >= 3
    assert (await _fresh(IntegrationJob, case["job_id"])).status == "failed"
    assert zendesk.sends == 0


@pytest.mark.asyncio
async def test_f01_job_deleted_during_delivery_is_reported_not_completed(
    db, org_scope, zendesk, monkeypatch
):
    """The job row is deleted by another session while delivery is failing.
    The failure branch must not crash, the message is still failed, and the
    run must not report the vanished job as completed."""
    from app.services.conversation_delivery_service import ConversationDeliveryService

    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id, external_id="not-a-number")
    original = ConversationDeliveryService.deliver

    async def _delete_job_then_deliver(self, *args, **kwargs):
        async with AsyncSessionLocal() as other:
            await other.execute(delete(IntegrationJob).where(IntegrationJob.id == case["job_id"]))
            await other.commit()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ConversationDeliveryService, "deliver", _delete_job_then_deliver)
    job = await _claim_specific(db, case["job_id"])
    result = await worker.process_claimed_job(db, job)

    assert result.outcome == "claim_lost"
    message = await _fresh(ConversationMessage, case["message_id"])
    assert message.delivery_status == "failed"
    assert message.delivery_error_code == "target_unavailable"
    assert zendesk.sends == 0


@pytest.mark.asyncio
async def test_f12_job_without_tenant_binding_is_terminal(db, org_scope, zendesk):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == case["job_id"])
            .values(organization_id=None)
        )
        await other.commit()

    await _run_job(db, case["job_id"])

    job = await _fresh(IntegrationJob, case["job_id"])
    assert job.status == "failed"
    assert zendesk.sends == 0
    async with AsyncSessionLocal() as other:  # unbound row: clean up explicitly
        await other.execute(delete(IntegrationJob).where(IntegrationJob.id == case["job_id"]))
        await other.commit()


# ---------------------------------------------------------------------------
# F03 / F04 - provider failure classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f03_transient_network_failure_retries_with_backoff(db, org_scope, zendesk):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    zendesk.fail_send = httpx.ConnectError("connection refused")
    before = datetime.now(UTC)

    await _run_job(db, case["job_id"])

    job = await _fresh(IntegrationJob, case["job_id"])
    message = await _fresh(ConversationMessage, case["message_id"])
    assert job.status == "retry"
    assert job.attempts == 1
    assert job.available_at > before
    assert job.locked_at is None
    assert message.delivery_status == "retrying"
    assert zendesk.sends == 0


@pytest.mark.asyncio
async def test_f04_permanent_provider_rejection_is_terminal(db, org_scope, zendesk):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    zendesk.fail_send = ZendeskAPIError("Zendesk API error 422", status_code=422)

    await _run_job(db, case["job_id"])

    job = await _fresh(IntegrationJob, case["job_id"])
    message = await _fresh(ConversationMessage, case["message_id"])
    assert job.status == "failed"
    assert message.delivery_status == "failed"
    assert message.delivery_error_code == "provider_rejected"
    assert zendesk.sends == 0


@pytest.mark.parametrize("status_code", [429, 500, 503])
@pytest.mark.asyncio
async def test_f04_throttling_and_server_errors_stay_retryable(
    db, org_scope, zendesk, status_code
):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    zendesk.fail_send = ZendeskAPIError(
        f"Zendesk API error {status_code}", status_code=status_code
    )

    await _run_job(db, case["job_id"])

    assert (await _fresh(IntegrationJob, case["job_id"])).status == "retry"


# ---------------------------------------------------------------------------
# F06 / F07 / F10 / F11 / F16 - exactly-once external delivery
# ---------------------------------------------------------------------------


async def _send_then_lose_local_commit(db, case, monkeypatch) -> None:
    """Attempt 1: the provider accepts the reply, then the local commit fails."""
    original = IntegrationJobRepository.mark_completed

    async def _commit_fails(*args, **kwargs):
        raise RuntimeError("database commit failed")

    monkeypatch.setattr(IntegrationJobRepository, "mark_completed", _commit_fails)
    await _run_job(db, case["job_id"])
    monkeypatch.setattr(IntegrationJobRepository, "mark_completed", original)
    job = await _fresh(IntegrationJob, case["job_id"])
    assert job.status == "retry"


@pytest.mark.asyncio
async def test_f06_commit_failure_after_provider_accept_recovers_without_resend(
    db, org_scope, zendesk, monkeypatch
):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)

    await _send_then_lose_local_commit(db, case, monkeypatch)
    assert zendesk.sends == 1
    # The SLA first-response step inside delivery commits after the provider
    # write, so the local "sent" may already be durable; either way the retry
    # must reconcile rather than resend.
    message = await _fresh(ConversationMessage, case["message_id"])
    assert message.delivery_status in {"sent", "retrying"}

    await _run_job(db, case["job_id"])

    assert zendesk.sends == 1, "recovery must reconcile, not resend"
    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1
    assert (await _fresh(IntegrationJob, case["job_id"])).status == "completed"
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"


class _ProcessDied(BaseException):
    """Simulates the worker process dying (not an ``Exception``: nothing in the
    application may catch it)."""


async def _provider_accepts_then_process_dies(db, case, zendesk, monkeypatch) -> None:
    """Attempt 1: Zendesk stores the reply, then the process dies before ANY
    local commit. The job is left ``processing``; recovery (simulated for this
    job only) makes it claimable again."""
    accepted = {"n": 0}
    real_apply = ZendeskClient.apply_agent_action

    async def _accept_then_die(self, *args, **kwargs):
        await real_apply(self, *args, **kwargs)
        accepted["n"] += 1
        raise _ProcessDied

    monkeypatch.setattr(ZendeskClient, "apply_agent_action", _accept_then_die)
    job = await _claim_specific(db, case["job_id"])
    with pytest.raises(_ProcessDied):
        await worker.process_claimed_job(db, job)
    await db.rollback()
    monkeypatch.setattr(ZendeskClient, "apply_agent_action", real_apply)
    assert accepted["n"] == 1 and zendesk.sends == 1
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "queued"
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == case["job_id"])
            .values(status="retry", locked_at=None)
        )
        await other.commit()


@pytest.mark.asyncio
async def test_f10_marker_on_a_long_ticket_is_found_newest_first(
    db, org_scope, zendesk, monkeypatch
):
    """More than one page of comments: the reply marker is the NEWEST comment,
    beyond Zendesk's first (oldest-first) page. The scan must still find it."""
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    zendesk.seed(case["zendesk_ticket_id"], 150)

    await _provider_accepts_then_process_dies(db, case, zendesk, monkeypatch)
    await _run_job(db, case["job_id"])

    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1
    assert zendesk.sends == 1
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"


@pytest.mark.asyncio
async def test_f16_marker_scan_failure_fails_closed(db, org_scope, zendesk, monkeypatch):
    """If the pre-send scan cannot run, sending would risk a duplicate: retry."""
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)

    await _provider_accepts_then_process_dies(db, case, zendesk, monkeypatch)
    zendesk.fail_scan_times = 1
    await _run_job(db, case["job_id"])

    assert zendesk.sends == 1, "no send without a successful marker scan"
    assert (await _fresh(IntegrationJob, case["job_id"])).status == "retry"

    await _run_job(db, case["job_id"])
    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"


@pytest.mark.asyncio
async def test_f11_locally_sent_reply_on_a_long_ticket_is_never_resent(
    db, org_scope, zendesk, monkeypatch
):
    """The local message is already ``sent`` but the job was not completed
    (commit lost). Re-running must short-circuit on the local state and make
    zero provider calls - even where a marker scan could not see the marker."""
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)
    zendesk.seed(case["zendesk_ticket_id"], 150)
    await _send_then_lose_local_commit(db, case, monkeypatch)
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"
    zendesk.fail_scan_times = 10**6  # any provider call would now fail loudly

    await _run_job(db, case["job_id"])

    assert zendesk.sends == 1
    assert (await _fresh(IntegrationJob, case["job_id"])).status == "completed"


@pytest.mark.asyncio
async def test_f11_repeated_execution_of_a_sent_reply_makes_no_provider_call(
    db, org_scope, zendesk
):
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)

    await _run_job(db, case["job_id"])
    assert zendesk.sends == 1
    for _ in range(3):
        await _run_job(db, case["job_id"])

    assert zendesk.sends == 1
    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1


@pytest.mark.asyncio
async def test_f07_interruption_after_send_recovers_exactly_once(
    db, org_scope, zendesk, monkeypatch
):
    """The process dies after the provider write and before any local commit:
    the job is left 'processing'. Stale recovery re-runs it; the marker scan
    makes the re-run a reconciliation, not a second send."""
    org_id = await org_scope()
    case = await _zendesk_reply(db, org_id)

    class Crash(BaseException):
        pass

    original = IntegrationJobRepository.get_by_id_unscoped

    async def _crash_after_send(db_, job_id):
        if zendesk.sends == 1:
            raise Crash
        return await original(db_, job_id)

    monkeypatch.setattr(IntegrationJobRepository, "get_by_id_unscoped", _crash_after_send)
    job = await _claim_specific(db, case["job_id"])
    with pytest.raises(Crash):
        await worker.process_claimed_job(db, job)
    monkeypatch.setattr(IntegrationJobRepository, "get_by_id_unscoped", original)
    await db.rollback()

    stuck = await _fresh(IntegrationJob, case["job_id"])
    assert stuck.status == "processing"
    # Recovery: the stale claim is retried (simulated here for this job only).
    async with AsyncSessionLocal() as other:
        await other.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == case["job_id"])
            .values(status="retry", locked_at=None)
        )
        await other.commit()

    await _run_job(db, case["job_id"])

    assert zendesk.sends == 1
    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1
    assert (await _fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"


# ---------------------------------------------------------------------------
# F05 - rollback inside a unit of work (savepoint discipline)
# ---------------------------------------------------------------------------


async def _local_ticket_with_conversation(db, org_id: int):
    ticket = Ticket(organization_id=org_id, subject="Local", description="Body.", source="api")
    db.add(ticket)
    await db.commit()
    conversation = await ConversationIngestionService.sync_ticket_conversation(
        db, ticket=ticket, organization_id=org_id
    )
    return ticket, conversation


def _race_once(monkeypatch, owner, name):
    """The first call returns None (a concurrent writer has not committed yet
    from this reader's point of view); later calls see the real row."""
    original = getattr(owner, name)
    state = {"raced": False}

    async def _racing(*args, **kwargs):
        if not state["raced"]:
            state["raced"] = True
            return None
        return await original(*args, **kwargs)

    monkeypatch.setattr(owner, name, _racing)


@pytest.mark.asyncio
async def test_f05_initial_message_race_keeps_loaded_objects_usable(db, org_scope, monkeypatch):
    org_id = await org_scope()
    ticket, conversation = await _local_ticket_with_conversation(db, org_id)
    first = await ConversationIngestionService.ingest_initial_message(
        db, conversation=conversation, ticket=ticket, organization_id=org_id
    )
    first_id = first.id

    _race_once(monkeypatch, ConversationMessageRepository, "get_by_dedupe_key")
    again = await ConversationIngestionService.ingest_initial_message(
        db, conversation=conversation, ticket=ticket, organization_id=org_id
    )

    assert again.id == first_id
    # Loaded objects were not expired by a full rollback (no implicit IO).
    assert ticket.subject == "Local"
    assert conversation.id is not None


@pytest.mark.asyncio
async def test_f05_agent_mirror_race_keeps_session_and_pending_work(db, org_scope, monkeypatch):
    org_id = await org_scope()
    ticket, _conversation = await _local_ticket_with_conversation(db, org_id)
    kwargs = {
        "ticket": ticket,
        "run_id": "run-1",
        "organization_id": org_id,
        "dedupe_suffix": "zendesk.send_reply",
        "direction": "outbound",
        "visibility": "public",
        "body": "Agent reply.",
        "provider": "cxops",
    }
    first = await ConversationIngestionService.ensure_agent_reply_message(db, **kwargs)
    assert first is not None
    first_id = first.id

    # Pending work of the enclosing unit of work (e.g. the run's executed state).
    ticket.priority = "urgent"
    _race_once(monkeypatch, ConversationMessageRepository, "get_by_dedupe_key")
    again = await ConversationIngestionService.ensure_agent_reply_message(db, **kwargs)

    assert again is not None and again.id == first_id
    assert ticket.priority == "urgent"
    await db.commit()
    assert (await _fresh(Ticket, ticket.id)).priority == "urgent", "pending work was lost"


@pytest.mark.asyncio
async def test_f05_conversation_create_race_keeps_ticket_usable(db, org_scope, monkeypatch):
    """Two syncs of the same Zendesk thread race: the loser's INSERT collides on
    (tenant, provider, external_thread_id) and must re-resolve the winner
    without expiring the ticket it is still working with."""
    org_id = await org_scope()
    ticket = Ticket(
        organization_id=org_id,
        subject="Threaded",
        description="Body.",
        source="zendesk",
        external_id="5151",
    )
    db.add(ticket)
    await db.commit()
    winner = await ConversationIngestionService.sync_ticket_conversation(
        db, ticket=ticket, organization_id=org_id
    )
    winner_id = winner.id

    _race_once(monkeypatch, ConversationRepository, "get_by_external_thread_for_tenant")
    _race_once(monkeypatch, ConversationRepository, "get_by_ticket_for_tenant")
    again = await ConversationIngestionService.sync_ticket_conversation(
        db, ticket=ticket, organization_id=org_id
    )

    assert again.id == winner_id
    assert ticket.subject == "Threaded"


@pytest.mark.asyncio
async def test_f05_provider_comment_batch_race_keeps_loaded_objects(db, org_scope, monkeypatch):
    org_id = await org_scope()
    ticket, conversation = await _local_ticket_with_conversation(db, org_id)
    conversation_id = conversation.id

    def _candidates():
        return [
            ConversationMessage(
                organization_id=org_id,
                conversation_id=conversation_id,
                provider="zendesk",
                external_message_id=f"ext-{n}",
                dedupe_key=f"zendesk_comment:ext-{n}",
                direction="inbound",
                visibility="public",
                body="Comment.",
            )
            for n in range(3)
        ]

    await ConversationIngestionService._insert_messages_deduplicated(
        db, conversation_id=conversation_id, organization_id=org_id, candidates=_candidates()[:2]
    )

    original = ConversationMessageRepository.list_by_external_message_ids_for_tenant
    state = {"raced": False}

    async def _stale_preview(*args, **kwargs):
        if not state["raced"]:
            state["raced"] = True
            return set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        ConversationMessageRepository, "list_by_external_message_ids_for_tenant", _stale_preview
    )
    await ConversationIngestionService._insert_messages_deduplicated(
        db, conversation_id=conversation_id, organization_id=org_id, candidates=_candidates()
    )

    assert ticket.subject == "Local"
    async with AsyncSessionLocal() as other:
        rows = (
            await other.execute(
                select(ConversationMessage.external_message_id).where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.external_message_id.is_not(None),
                )
            )
        ).scalars().all()
    assert sorted(rows) == ["ext-0", "ext-1", "ext-2"]


@pytest.mark.asyncio
async def test_f05_sla_scanner_race_keeps_other_milestone_and_ticket(db, org_scope, monkeypatch):
    from app.services.sla_escalation_scanner_service import SLAEscalationScannerService

    org_id = await org_scope()
    now = datetime.now(UTC)
    ticket = Ticket(
        organization_id=org_id,
        subject="Breached",
        description=".",
        first_response_due_at=now - timedelta(hours=2),
        resolution_due_at=now - timedelta(hours=1),
    )
    db.add(ticket)
    await db.commit()
    ticket_id = ticket.id

    # The first-response job already exists (another scanner won that race).
    first = await SLAEscalationScannerService._enqueue_milestone_jobs(
        db, ticket_id=ticket_id, organization_id=org_id, now=now
    )
    assert first == 2
    async with AsyncSessionLocal() as other:
        await other.execute(
            delete(IntegrationJob).where(
                IntegrationJob.organization_id == org_id,
                IntegrationJob.dedupe_key.like("%resolution%"),
            )
        )
        await other.commit()

    monkeypatch.setattr(
        IntegrationJobRepository,
        "get_by_dedupe_key",
        staticmethod(lambda *_a, **_k: _none()),
    )
    enqueued = await SLAEscalationScannerService._enqueue_milestone_jobs(
        db, ticket_id=ticket_id, organization_id=org_id, now=now
    )

    assert enqueued == 1, "the resolution job must still be enqueued after the collision"
    async with AsyncSessionLocal() as other:
        jobs = (
            await other.execute(
                select(IntegrationJob.dedupe_key).where(IntegrationJob.organization_id == org_id)
            )
        ).scalars().all()
    assert len(jobs) == 2


async def _none():
    return None


# ---------------------------------------------------------------------------
# F08 / F09 / F14 / F15 - claims, stale recovery, fencing, capacity
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def isolated_queue():
    """Only for disposable databases: start from an empty claimable queue."""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(IntegrationJob))
        await session.commit()
    yield


async def _pending_jobs(org_id: int, count: int, **values) -> list[int]:
    async with AsyncSessionLocal() as session:
        jobs = [
            IntegrationJob(
                organization_id=org_id,
                dedupe_key=f"fault:{uuid.uuid4().hex}",
                job_type="fault.noop",
                payload={},
                **values,
            )
            for _ in range(count)
        ]
        session.add_all(jobs)
        await session.commit()
        return [job.id for job in jobs]


@requires_disposable_db
@pytest.mark.asyncio
async def test_f09_concurrent_claims_never_double_claim(org_scope, isolated_queue):
    org_id = await org_scope()
    ids = await _pending_jobs(org_id, 6)

    async def _drain() -> list[int]:
        claimed = []
        while True:
            async with AsyncSessionLocal() as session:
                job = await IntegrationJobRepository.claim_next_unscoped(session)
                if job is None:
                    return claimed
                claimed.append(job.id)

    first, second = await asyncio.gather(_drain(), _drain())

    assert sorted(first + second) == sorted(ids)
    assert not set(first) & set(second)
    for job_id in ids:
        job = await _fresh(IntegrationJob, job_id)
        assert job.status == "processing" and job.attempts == 1


@requires_disposable_db
@pytest.mark.asyncio
async def test_f08_live_long_running_job_is_not_stolen(org_scope, isolated_queue):
    """A job legitimately running for two minutes (inside the one-shot budget)
    must not be 'recovered' and executed a second time concurrently."""
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(
        org_id,
        1,
        status="processing",
        attempts=1,
        locked_at=datetime.now(UTC) - timedelta(seconds=120),
    )

    async with AsyncSessionLocal() as session:
        claimed = await IntegrationJobRepository.claim_next_unscoped(session)

    assert claimed is None
    job = await _fresh(IntegrationJob, job_id)
    assert job.status == "processing" and job.attempts == 1


@requires_disposable_db
@pytest.mark.asyncio
async def test_f08_crashed_job_is_recovered_after_the_stale_window(org_scope, isolated_queue):
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(
        org_id,
        1,
        status="processing",
        attempts=1,
        locked_at=datetime.now(UTC) - timedelta(hours=1),
    )

    async with AsyncSessionLocal() as session:
        claimed = await IntegrationJobRepository.claim_next_unscoped(session)

    assert claimed is not None and claimed.id == job_id
    assert claimed.attempts == 2


@requires_disposable_db
@pytest.mark.asyncio
async def test_f08_poison_job_stops_at_max_attempts(org_scope, isolated_queue):
    """A job that crashes the worker every time must not be recovered forever:
    attempts may never exceed max_attempts (seen locally as attempts=6/5)."""
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(
        org_id,
        1,
        status="processing",
        attempts=5,
        max_attempts=5,
        locked_at=datetime.now(UTC) - timedelta(hours=1),
    )

    async with AsyncSessionLocal() as session:
        claimed = await IntegrationJobRepository.claim_next_unscoped(session)

    assert claimed is None
    job = await _fresh(IntegrationJob, job_id)
    assert job.status == "failed"
    assert job.attempts == 5
    assert job.locked_at is None


@pytest.mark.asyncio
async def test_f15_lost_claim_cannot_overwrite_a_newer_claim(org_scope):
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(org_id, 1)
    first_claim = datetime.now(UTC) - timedelta(hours=1)
    second_claim = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(status="processing", attempts=2, locked_at=second_claim)
        )
        await session.commit()

    # Worker A (claimed at first_claim) finishes late and reports.
    async with AsyncSessionLocal() as session:
        failed = await IntegrationJobRepository.mark_failed(
            session, job_id=job_id, error_message="late", claimed_at=first_claim
        )
        completed = await IntegrationJobRepository.mark_completed(
            session, job_id=job_id, claimed_at=first_claim
        )

    assert failed is None and completed is None
    job = await _fresh(IntegrationJob, job_id)
    assert job.status == "processing"
    assert job.locked_at == second_claim
    assert job.attempts == 2


@pytest.mark.asyncio
async def test_f15_current_claim_can_complete(org_scope):
    org_id = await org_scope()
    (job_id,) = await _pending_jobs(org_id, 1)
    claim = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(status="processing", attempts=1, locked_at=claim)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        completed = await IntegrationJobRepository.mark_completed(
            session, job_id=job_id, claimed_at=claim
        )

    assert completed is not None and completed.status == "completed"


@requires_disposable_db
@pytest.mark.asyncio
async def test_f14_capacity_bound_leaves_unclaimed_jobs_untouched(
    org_scope, isolated_queue, monkeypatch
):
    org_id = await org_scope()
    ids = await _pending_jobs(org_id, 3)

    async def _noop_execute(db, job):
        return None

    monkeypatch.setattr(worker.IntegrationJobService, "execute", _noop_execute)

    async def _no_scan():
        return "skipped"

    monkeypatch.setattr(worker, "run_sla_scan_cycle", _no_scan)
    summary = await worker.run_worker_once(max_jobs=2, max_seconds=240)

    assert summary["jobs_processed"] == 2
    assert summary["exit_reason"] == "max_jobs"
    statuses = [(await _fresh(IntegrationJob, job_id)).status for job_id in ids]
    assert statuses == ["completed", "completed", "pending"]
    untouched = await _fresh(IntegrationJob, ids[2])
    assert untouched.attempts == 0 and untouched.locked_at is None


# ---------------------------------------------------------------------------
# F13 - AI evaluation failures
# ---------------------------------------------------------------------------


async def _ai_eval_job(org_id: int, *, target_type: str) -> int:
    async with AsyncSessionLocal() as session:
        job = IntegrationJob(
            organization_id=org_id,
            dedupe_key=f"ai-evaluation:{org_id}:{uuid.uuid4().hex}",
            job_type="ai.evaluation",
            payload={"evaluation_run_id": "run-x", "target_type": target_type},
        )
        session.add(job)
        await session.commit()
        return job.id


@pytest.mark.asyncio
async def test_f13_unsupported_ai_evaluation_target_is_terminal(db, org_scope):
    org_id = await org_scope()
    job_id = await _ai_eval_job(org_id, target_type="vision")

    await _run_job(db, job_id)

    job = await _fresh(IntegrationJob, job_id)
    assert job.status == "failed"
    assert job.attempts == 1


@pytest.mark.asyncio
async def test_f13_ai_evaluation_dependency_failure_is_bounded_and_sanitized(
    db, org_scope, monkeypatch
):
    import builtins

    org_id = await org_scope()
    job_id = await _ai_eval_job(org_id, target_type="rag")
    real_import = builtins.__import__

    def _broken_import(name, *args, **kwargs):
        if name == "app.services.ai_evaluation_service":
            raise ImportError("No module named 'langchain_core'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken_import)
    monkeypatch.delitem(__import__("sys").modules, "app.services.ai_evaluation_service", raising=False)

    for _attempt in range(5):
        await _run_job(db, job_id)
        job = await _fresh(IntegrationJob, job_id)
        if job.status == "failed":
            break
        async with AsyncSessionLocal() as other:  # skip the backoff wait
            await other.execute(
                update(IntegrationJob).where(IntegrationJob.id == job_id).values(available_at=datetime.now(UTC))
            )
            await other.commit()

    job = await _fresh(IntegrationJob, job_id)
    assert job.status == "failed"
    assert job.attempts == job.max_attempts
    assert "langchain_core" not in (job.last_error or ""), "error text must be sanitized"
    assert "ImportError" in (job.last_error or "")
