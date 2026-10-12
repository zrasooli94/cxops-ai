"""Phase 1P.8R second review of PR #4: reproducers for defects the first pass
missed, against real PostgreSQL (no mocks of the database)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.ticket import Ticket
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.integration_job_repository import IntegrationJobRepository
from app.repositories.savepoint import insert_in_savepoint
from app.services.conversation_ingestion_service import ConversationIngestionService
from app.services.conversation_reply_service import ConversationReplyService
from tests import test_job_fault_injection as harness

_no_network = harness._no_network
org_scope = harness.org_scope
zendesk = harness.zendesk


@pytest.mark.asyncio
async def test_savepoint_never_misreports_the_callers_own_pending_conflict(db, org_scope):
    """``begin_nested()`` flushes the caller's PENDING objects first. A unique
    violation in that pending work is the caller's bug, not "our insert lost a
    race": it must surface, not be returned as a collision."""
    org_id = await org_scope()
    ticket = Ticket(organization_id=org_id, subject="s", description="d", source="api")
    db.add(ticket)
    await db.commit()
    conversation = await ConversationIngestionService.sync_ticket_conversation(
        db, ticket=ticket, organization_id=org_id
    )
    conversation_id = conversation.id
    first = await ConversationIngestionService.ingest_initial_message(
        db, conversation=conversation, ticket=ticket, organization_id=org_id
    )

    # Caller's own pending duplicate (same conversation + dedupe key).
    db.add(
        ConversationMessage(
            organization_id=org_id,
            conversation_id=conversation_id,
            provider="cxops",
            dedupe_key=first.dedupe_key,
            direction="inbound",
            visibility="public",
            body="duplicate",
        )
    )
    innocent = ConversationMessage(
        organization_id=org_id,
        conversation_id=conversation_id,
        provider="cxops",
        dedupe_key=f"innocent:{uuid.uuid4().hex}",
        direction="inbound",
        visibility="public",
        body="ok",
    )

    with pytest.raises(IntegrityError):
        await insert_in_savepoint(db, innocent)
    await db.rollback()


@pytest.mark.asyncio
async def test_concurrent_duplicate_enqueue_returns_the_winner(db, org_scope, monkeypatch):
    """Two requests with the same client_request_id race; the loser's commit
    collides. Its recovery path must not read expired ORM state after the
    rollback (MissingGreenlet) - it must return the winner as a duplicate."""
    org_id = await org_scope()
    ticket = Ticket(
        organization_id=org_id,
        subject="s",
        description="d",
        source="zendesk",
        external_id="6060",
    )
    db.add(ticket)
    await db.commit()
    conversation = await ConversationIngestionService.sync_ticket_conversation(
        db, ticket=ticket, organization_id=org_id
    )
    request_id = str(uuid.uuid4())
    kwargs = {
        "conversation_id": conversation.id,
        "body": "Hello",
        "client_request_id": request_id,
        "organization_id": org_id,
        "requested_by_subject": "agent-1",
    }
    winner = await ConversationReplyService.enqueue_reply(db, **kwargs)

    # The loser's pre-check ran before the winner committed.
    original = ConversationMessageRepository.get_by_dedupe_key
    state = {"raced": False}

    async def _not_yet_visible(*args, **kw):
        if not state["raced"]:
            state["raced"] = True
            return None
        return await original(*args, **kw)

    monkeypatch.setattr(
        ConversationMessageRepository, "get_by_dedupe_key", staticmethod(_not_yet_visible)
    )
    async with AsyncSessionLocal() as loser_db:
        loser = await ConversationReplyService.enqueue_reply(loser_db, **kwargs)

    assert loser["duplicate"] is True
    assert loser["message_id"] == winner["message_id"]
    assert loser["job_id"] == winner["job_id"]


@pytest.mark.asyncio
async def test_fenced_update_rechecks_after_waiting_on_a_row_lock(org_scope):
    """READ COMMITTED + row lock: A's fenced UPDATE blocks on B's uncommitted
    re-claim. When B commits, PostgreSQL re-evaluates A's WHERE against the new
    row version (EvalPlanQual) and A matches nothing. Observed via
    pg_stat_activity, not timing."""
    org_id = await org_scope()
    (job_id,) = await harness._pending_jobs(org_id, 1)
    a_claim = datetime.now(UTC) - timedelta(hours=1)
    b_claim = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(status="processing", locked_at=a_claim, attempts=1)
        )
        await session.commit()

    async with AsyncSessionLocal() as b_session, AsyncSessionLocal() as a_session:
        await b_session.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(locked_at=b_claim, attempts=2)
        )  # B holds the row lock, uncommitted
        a_pid = (await a_session.execute(text("SELECT pg_backend_pid()"))).scalar_one()

        a_task = asyncio.create_task(
            IntegrationJobRepository.mark_failed(
                a_session, job_id=job_id, error_message="late", claimed_at=a_claim
            )
        )
        async with AsyncSessionLocal() as observer:
            for _ in range(500):
                waiting = (
                    await observer.execute(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": a_pid},
                    )
                ).scalar_one_or_none()
                await observer.rollback()
                if waiting == "Lock":
                    break
                await asyncio.sleep(0.01)
            assert waiting == "Lock", "A's fenced UPDATE should be waiting on B's row lock"

        await b_session.commit()
        result = await asyncio.wait_for(a_task, 10)

    assert result is None
    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(select(IntegrationJob).where(IntegrationJob.id == job_id))
        ).scalar_one()
    assert job.status == "processing" and job.locked_at == b_claim
