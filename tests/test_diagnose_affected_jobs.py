"""The affected-job diagnostic must classify correctly, stay strictly read-only,
refuse remote databases by default, and never print customer content."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import AsyncSessionLocal
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.ticket import Ticket
from scripts import diagnose_affected_jobs as diag

GREENLET_ERROR = (
    "greenlet_spawn has not been called; can't call await_only() here. "
    "Was IO attempted in an unexpected place?"
)
BODY = "CUSTOMER-SECRET-BODY-" + uuid.uuid4().hex


@pytest_asyncio.fixture
async def seeded():
    """One affected reply job per recovery category, in its own tenant."""
    async with AsyncSessionLocal() as session:
        org = Organization(name=f"diag-test-{uuid.uuid4().hex[:8]}")
        other_org = Organization(name=f"diag-test-{uuid.uuid4().hex[:8]}")
        session.add_all([org, other_org])
        await session.flush()

        async def reply_case(
            *, provider, ticket_source, external_id, conv_status, delivery, token=True
        ):
            ticket = Ticket(
                organization_id=org.id,
                subject="s",
                description="d",
                source=ticket_source,
                external_id=external_id,
            )
            session.add(ticket)
            await session.flush()
            conversation = Conversation(
                organization_id=org.id,
                ticket_id=ticket.id,
                provider=provider,
                channel="ticket",
                external_thread_id=external_id if provider == "zendesk" else None,
                status=conv_status,
            )
            session.add(conversation)
            await session.flush()
            message = ConversationMessage(
                organization_id=org.id,
                conversation_id=conversation.id,
                provider=provider,
                dedupe_key=f"human_reply:{uuid.uuid4().hex}",
                direction="outbound",
                visibility="public",
                body=BODY,
                delivery_status=delivery,
                delivery_token=str(uuid.uuid4()) if token else None,
            )
            session.add(message)
            await session.flush()
            job = IntegrationJob(
                organization_id=org.id,
                dedupe_key=f"diag:{uuid.uuid4().hex}",
                job_type="conversation.reply",
                payload={"message_id": message.id, "secret": BODY},
                status="retry",
                attempts=1,
                last_error=GREENLET_ERROR,
            )
            session.add(job)
            await session.flush()
            return job.id

        ids = {
            "verify": await reply_case(
                provider="zendesk", ticket_source="zendesk", external_id="777",
                conv_status="open", delivery="retrying",
            ),
            "terminal": await reply_case(
                provider="zendesk", ticket_source="zendesk", external_id="not-numeric",
                conv_status="open", delivery="retrying",
            ),
            "closed": await reply_case(
                provider="zendesk", ticket_source="zendesk", external_id="778",
                conv_status="closed", delivery="retrying",
            ),
            "sent": await reply_case(
                provider="zendesk", ticket_source="zendesk", external_id="779",
                conv_status="open", delivery="sent",
            ),
            "local": await reply_case(
                provider="cxops", ticket_source="api", external_id=None,
                conv_status="open", delivery="retrying",
            ),
            # The real local shape: a legacy reply without a delivery token.
            "tokenless": await reply_case(
                provider="zendesk", ticket_source="zendesk", external_id="780",
                conv_status="open", delivery="retrying", token=False,
            ),
        }
        # Foreign tenant: job bound to other_org pointing at org's message.
        foreign_message_id = (
            await session.execute(
                select(IntegrationJob.payload["message_id"].as_integer()).where(
                    IntegrationJob.id == ids["verify"]
                )
            )
        ).scalar_one()
        foreign = IntegrationJob(
            organization_id=other_org.id,
            dedupe_key=f"diag:{uuid.uuid4().hex}",
            job_type="conversation.reply",
            payload={"message_id": foreign_message_id},
            status="retry",
            attempts=1,
            last_error=GREENLET_ERROR,
        )
        overflow = IntegrationJob(
            organization_id=org.id,
            dedupe_key=f"diag:{uuid.uuid4().hex}",
            job_type="conversation.reply",
            payload={"message_id": foreign_message_id},
            status="failed",
            attempts=6,
            max_attempts=5,
            last_error=GREENLET_ERROR,
        )
        session.add_all([foreign, overflow])
        await session.commit()
        ids["foreign"] = foreign.id
        ids["overflow"] = overflow.id
        org_ids = [org.id, other_org.id]

    yield ids

    async with AsyncSessionLocal() as session:
        for name in ("integration_jobs", "conversation_messages", "conversations", "tickets"):
            table = Base.metadata.tables[name]
            await session.execute(delete(table).where(table.c.organization_id.in_(org_ids)))
        await session.execute(delete(Organization).where(Organization.id.in_(org_ids)))
        await session.commit()


async def _collect() -> dict[int, diag.JobRow]:
    async with AsyncSessionLocal() as session:
        try:
            rows = await diag.collect(session, limit=10000)
        finally:
            await session.rollback()
    return {row.job_id: row for row in rows}


@pytest.mark.asyncio
async def test_every_affected_job_lands_in_the_right_recovery_category(seeded) -> None:
    rows = await _collect()

    expected = {
        "verify": diag.NEEDS_PROVIDER_VERIFICATION,
        "terminal": diag.TERMINAL_TARGET,
        "closed": diag.TERMINAL_TARGET,
        "sent": diag.ALREADY_SENT,
        "local": diag.LOCAL_REDELIVERY,
        "tokenless": diag.TERMINAL_TARGET,
        "foreign": diag.LEAVE_UNTOUCHED,
        "overflow": diag.ATTEMPTS_OVERFLOW,
    }
    for name, category in expected.items():
        assert rows[seeded[name]].recovery_category == category, name
        assert rows[seeded[name]].error_category == "missing_greenlet"
    assert rows[seeded["foreign"]].message_found is False


@pytest.mark.asyncio
async def test_the_transaction_is_really_read_only(seeded) -> None:
    async with AsyncSessionLocal() as session:
        await diag.collect(session, limit=10)
        with pytest.raises(DBAPIError) as excinfo:
            await session.execute(
                text("UPDATE integration_jobs SET attempts = attempts WHERE id = :id"),
                {"id": seeded["verify"]},
            )
        assert "read-only" in str(excinfo.value).lower()
        await session.rollback()


@pytest.mark.asyncio
async def test_nothing_is_modified(seeded) -> None:
    async def snapshot():
        async with AsyncSessionLocal() as session:
            jobs = (
                await session.execute(
                    select(IntegrationJob.id, IntegrationJob.status, IntegrationJob.attempts)
                    .where(IntegrationJob.id.in_(list(seeded.values())))
                    .order_by(IntegrationJob.id)
                )
            ).all()
            return [tuple(row) for row in jobs]

    before = await snapshot()
    await _collect()
    assert await snapshot() == before


@pytest.mark.parametrize("as_json", [True, False])
@pytest.mark.asyncio
async def test_output_never_contains_bodies_or_payload_content(seeded, as_json) -> None:
    rows = list((await _collect()).values())
    out = diag.render_output(rows, database_category="local", as_json=as_json)
    assert str(seeded["verify"]) in out
    assert BODY not in out
    assert "secret" not in out.lower()


def test_remote_database_is_refused_by_default(monkeypatch, capsys) -> None:
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+asyncpg://u:p@ep-x.us-east-2.aws.neon.tech/neondb",
        raising=False,
    )

    def _must_not_run(_limit):
        raise AssertionError("must not touch a remote database")

    monkeypatch.setattr(diag, "_run", _must_not_run)
    assert diag.main([]) == 1
    err = capsys.readouterr().err
    assert "refusing" in err and "neon" in err
    assert "u:p" not in err


@pytest.mark.parametrize(
    ("facts", "category"),
    [
        ({"attempts": 6, "max_attempts": 5}, diag.ATTEMPTS_OVERFLOW),
        ({"job_type": "ai.evaluation"}, diag.LEAVE_UNTOUCHED),
        ({"message_found": False}, diag.LEAVE_UNTOUCHED),
        ({"delivery_status": "sent"}, diag.ALREADY_SENT),
        ({"provider": "cxops"}, diag.LOCAL_REDELIVERY),
        ({"provider": "sms"}, diag.TERMINAL_TARGET),
        ({"conversation_status": "closed"}, diag.TERMINAL_TARGET),
        ({"ticket_external_id_numeric": False}, diag.TERMINAL_TARGET),
        ({"has_delivery_token": False}, diag.TERMINAL_TARGET),
        ({"thread_matches_ticket": False}, diag.TERMINAL_TARGET),
        ({}, diag.NEEDS_PROVIDER_VERIFICATION),
    ],
)
def test_classify_rules(facts, category) -> None:
    base = {
        "attempts": 1,
        "max_attempts": 5,
        "job_type": "conversation.reply",
        "message_found": True,
        "delivery_status": "retrying",
        "provider": "zendesk",
        "conversation_status": "open",
        "ticket_source": "zendesk",
        "ticket_external_id_numeric": True,
        "has_delivery_token": True,
        "thread_matches_ticket": True,
    }
    assert diag.classify({**base, **facts})[0] == category


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (GREENLET_ERROR, "missing_greenlet"),
        ("sqlalchemy.exc.MissingGreenlet: x", "missing_greenlet"),
        ("Recovered stale processing job", "stale_recovered"),
        ("boom", "other"),
        (None, "none"),
    ],
)
def test_error_category(error, category) -> None:
    assert diag.error_category(error) == category


# ---------------------------------------------------------------------------
# Phase 1P.8R: consistency, completeness, sanitization, decision workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_is_one_consistent_snapshot_under_concurrent_writes(seeded, monkeypatch):
    """A worker marks a reply sent WHILE the diagnostic runs: the report must
    still describe one moment (REPEATABLE READ), not a mix of two."""
    original = diag._fetch_message
    state = {"done": False}

    async def _concurrent_write_then_fetch(session, *, message_id, organization_id):
        if not state["done"]:
            state["done"] = True
            async with AsyncSessionLocal() as writer:
                await writer.execute(
                    text(
                        "UPDATE conversation_messages SET delivery_status = 'sent' "
                        "WHERE organization_id = :org"
                    ),
                    {"org": organization_id},
                )
                await writer.commit()
        return await original(session, message_id=message_id, organization_id=organization_id)

    monkeypatch.setattr(diag, "_fetch_message", _concurrent_write_then_fetch)
    async with AsyncSessionLocal() as session:
        try:
            report = await diag.collect_report(session, limit=10000)
        finally:
            await session.rollback()

    rows = {r.job_id: r for r in report.rows}
    assert rows[seeded["verify"]].delivery_status == "retrying", "saw a later write"
    assert rows[seeded["sent"]].delivery_status == "sent"


@pytest.mark.asyncio
async def test_more_rows_than_the_limit_is_reported_incomplete(seeded):
    async with AsyncSessionLocal() as session:
        try:
            report = await diag.collect_report(session, limit=2)
        finally:
            await session.rollback()

    assert report.truncated is True
    assert len(report.rows) == 2
    text_report = diag.render_operator_report(report, database_category="local")
    assert "INCOMPLETE" in text_report
    rendered = diag.render_output(
        report.rows, database_category="local", as_json=True, truncated=True
    )
    assert '"complete": false' in rendered


def test_truncated_run_exits_3(monkeypatch, capsys) -> None:
    async def _partial(_limit):
        return diag.DiagnosticReport(rows=[], truncated=True, snapshot_at="t")

    monkeypatch.setattr(diag, "_run", _partial)
    assert diag.main([]) == 3


def test_driver_errors_are_reported_without_details(monkeypatch, capsys) -> None:
    async def _boom(_limit):
        raise OSError("could not connect to postgresql://admin:s3cret@db.internal:5432")

    monkeypatch.setattr(diag, "_run", _boom)
    assert diag.main([]) == 1
    err = capsys.readouterr().err
    assert "OSError" in err
    assert "s3cret" not in err and "admin" not in err and "db.internal" not in err


@pytest.mark.parametrize(
    ("category", "status", "age", "action", "auto"),
    [
        (diag.ATTEMPTS_OVERFLOW, "failed", 1.0, diag.ACTION_LEAVE, False),
        (diag.LEAVE_UNTOUCHED, "retry", 1.0, diag.ACTION_LEAVE, True),
        (diag.ALREADY_SENT, "retry", 1.0, diag.ACTION_RECONCILE_COMPLETED, True),
        (diag.TERMINAL_TARGET, "retry", 1.0, diag.ACTION_SUPERVISED_FAIL, True),
        (diag.TERMINAL_TARGET, "failed", 1.0, diag.ACTION_SUPERVISED_FAIL, False),
        (diag.LOCAL_REDELIVERY, "retry", 1.0, diag.ACTION_LOCAL_REDELIVER, True),
        (diag.MANUAL_MARKER_VERIFICATION, "failed", 1.0, diag.ACTION_OPERATOR_VERIFY, False),
        (diag.NEEDS_PROVIDER_VERIFICATION, "retry", 2.0, diag.ACTION_VERIFY_THEN_DECIDE, True),
        (diag.NEEDS_PROVIDER_VERIFICATION, "retry", 72.0, diag.ACTION_LATE_REPLY, True),
        (diag.NEEDS_PROVIDER_VERIFICATION, "processing", None, diag.ACTION_VERIFY_THEN_DECIDE, True),
    ],
)
def test_decision_rules(category, status, age, action, auto) -> None:
    assert diag.decide(category, status=status, age_hours=age) == (action, auto)


def test_every_action_names_its_approval() -> None:
    for action in (
        diag.ACTION_LEAVE,
        diag.ACTION_RECONCILE_COMPLETED,
        diag.ACTION_SUPERVISED_FAIL,
        diag.ACTION_LOCAL_REDELIVER,
        diag.ACTION_VERIFY_THEN_DECIDE,
        diag.ACTION_LATE_REPLY,
        diag.ACTION_OPERATOR_VERIFY,
    ):
        assert diag.APPROVALS[action]


def test_delivery_unverified_is_its_own_category() -> None:
    base = {
        "attempts": 1,
        "max_attempts": 5,
        "job_type": "conversation.reply",
        "message_found": True,
        "delivery_status": "failed",
        "delivery_error_code": "delivery_unverified",
        "provider": "zendesk",
        "conversation_status": "open",
        "ticket_source": "zendesk",
        "ticket_external_id_numeric": True,
        "has_delivery_token": True,
        "thread_matches_ticket": True,
    }
    assert diag.classify(base)[0] == diag.MANUAL_MARKER_VERIFICATION


@pytest.mark.asyncio
async def test_late_valid_target_reply_needs_a_human_decision(seeded):
    """A reply whose target became valid days later must not just be sent."""
    async with AsyncSessionLocal() as session:
        message_id = (
            await session.execute(
                select(IntegrationJob.payload["message_id"].as_integer()).where(
                    IntegrationJob.id == seeded["verify"]
                )
            )
        ).scalar_one()
        await session.execute(
            text(
                "UPDATE conversation_messages SET created_at = now() - interval '3 days' "
                "WHERE id = :id"
            ),
            {"id": message_id},
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        try:
            report = await diag.collect_report(session, limit=10000)
        finally:
            await session.rollback()

    row = next(r for r in report.rows if r.job_id == seeded["verify"])
    assert row.recommended_action == diag.ACTION_LATE_REPLY
    assert row.message_age_hours is not None and row.message_age_hours >= 72
    assert row.auto_executes_after_deploy is True
    summary = diag.render_operator_report(report, database_category="local")
    assert "executed automatically" in summary
    assert "late_reply_human_decision" in summary
    assert BODY not in summary
