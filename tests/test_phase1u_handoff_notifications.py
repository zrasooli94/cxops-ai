"""Phase 1U-A: feature-disabled staff alerts; NO external notifications.

Database tests use the disposable-test namespace and fake delivery. No SMTP,
Telegram, Apprise network call or production credential is used.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.integration_job import IntegrationJob
from app.models.public_chat import PublicChatSession
from app.repositories.public_chat_repository import PublicChatRepository
from app.services import handoff_notification_service as notifications
from app.services.apprise_sender import NotificationDestinationError, restricted_url
from app.services.integration_job_service import JobDeferredError
from tests import test_public_chat as base

_configure_auth = base._configure_auth
_phase1p_namespace_isolated = base._phase1p_namespace_isolated
client = base.client
db = base.db
org_scope = base.org_scope


@pytest.mark.parametrize(
    "url",
    [
        "json://example.org", "xml://example.org",
        "form://example.org", "forms://example.org",
        "windows://", "dbus://", "file:///etc/passwd",
        "syslog://", "include://example.org", "https://example.org",
        "mailto://user:pass@localhost?to=me@example.org",
    ],
)
def test_dangerous_apprise_schemes_rejected_without_network(url):
    with pytest.raises(NotificationDestinationError):
        restricted_url("email", url)
    with pytest.raises(NotificationDestinationError):
        restricted_url("telegram", url)


def test_restricted_telegram_url_injects_socket_timeouts():
    clean = restricted_url(
        "telegram",
        "tgram://123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef/123456789",
    )
    assert clean.startswith("tgram://")
    assert "cto=2" in clean and "rto=4" in clean
    with pytest.raises(NotificationDestinationError):
        restricted_url("telegram", "tgram://fake/123?redirect=yes")


def test_restricted_email_requires_tls_approved_host_and_one_recipient():
    good = (
        "mailtos://user:secret@company.example"
        "?smtp=smtp.gmail.com&to=staff@example.com"
    )
    clean = restricted_url("email", good)
    assert "cto=2" in clean and "rto=4" in clean
    for bad in (
        good.replace("smtp.gmail.com", "127.0.0.1"),
        good + "&to=other@example.com",
        good + "&redirect=yes",
        good.replace("mailtos://", "mailto://"),
    ):
        with pytest.raises(NotificationDestinationError):
            restricted_url("email", bad)


async def _session(db, client, org_scope):
    org = await org_scope()
    org.external_id = "rispu"
    await db.commit()
    _cfg, key = await base._make_widget_config(db, org)
    token = (await base._create_session(client, key))["session"]["token"]
    session = await db.scalar(
        select(PublicChatSession)
        .where(PublicChatSession.organization_id == org.id)
        .order_by(PublicChatSession.id.desc())
    )
    assert session is not None and token
    return org, session


def _enable(monkeypatch):
    monkeypatch.setattr(notifications.settings, "handoff_notifications_enabled", True)
    monkeypatch.setattr(
        notifications.settings, "handoff_notification_activated_at",
        datetime.now(UTC) - timedelta(minutes=5),
    )
    monkeypatch.setattr(
        notifications.settings, "handoff_notification_pilot_tenant", "rispu"
    )


@pytest.mark.asyncio
async def test_feature_disabled_handoff_stages_no_job(db, client, org_scope, monkeypatch):
    monkeypatch.setattr(notifications.settings, "handoff_notifications_enabled", False)
    org, session = await _session(db, client, org_scope)
    changed = await PublicChatRepository.handoff_to_human(
        db, session=session, organization_id=org.id
    )
    assert changed.status == "human_requested"
    jobs = (
        await db.execute(
            select(IntegrationJob).where(
                IntegrationJob.organization_id == org.id,
                IntegrationJob.job_type == notifications.NOTIFY_JOB_TYPE,
            )
        )
    ).scalars().all()
    assert jobs == []


@pytest.mark.asyncio
async def test_first_handoff_is_atomic_and_duplicate_safe(db, client, org_scope, monkeypatch):
    _enable(monkeypatch)
    from app.services.public_chat_turn_dispatcher import handoff_dispatcher
    submitted: list[int] = []
    monkeypatch.setattr(handoff_dispatcher, "submit", lambda job_id, _org: submitted.append(job_id))
    org, session = await _session(db, client, org_scope)

    result = await PublicChatRepository.handoff_to_human(
        db, session=session, organization_id=org.id
    )
    assert result.status == "human_requested"
    result = await PublicChatRepository.handoff_to_human(
        db, session=session, organization_id=org.id
    )
    assert result.status == "human_requested"

    jobs = (
        await db.execute(
            select(IntegrationJob).where(
                IntegrationJob.organization_id == org.id,
                IntegrationJob.job_type == notifications.NOTIFY_JOB_TYPE,
            )
        )
    ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].dedupe_key == notifications.first_handoff_key(org.id, session.id)
    assert jobs[0].payload == {"event": "initial", "session_id": session.id}
    assert submitted == [jobs[0].id]
    assert "mailto" not in str(jobs[0].payload)
    assert "tgram" not in str(jobs[0].payload)


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_trigger_handoff_job(db, client, org_scope, monkeypatch):
    _enable(monkeypatch)
    _org, session = await _session(db, client, org_scope)
    other = await org_scope()
    with pytest.raises(ValueError, match="does not belong"):
        await PublicChatRepository.handoff_to_human(
            db, session=session, organization_id=other.id
        )
    await db.rollback()
    found = await db.scalar(
        select(IntegrationJob.id).where(
            IntegrationJob.dedupe_key == notifications.first_handoff_key(
                other.id, session.id
            )
        )
    )
    assert found is None


@pytest.mark.asyncio
async def test_unconfigured_recovery_worker_defers_before_delivery(
    db, client, org_scope, monkeypatch
):
    _enable(monkeypatch)
    monkeypatch.delenv("HANDOFF_NOTIFICATIONS_ENABLED", raising=False)
    from app.services.public_chat_turn_dispatcher import handoff_dispatcher
    monkeypatch.setattr(handoff_dispatcher, "submit", lambda *_a: True)
    org, session = await _session(db, client, org_scope)
    await PublicChatRepository.handoff_to_human(
        db, session=session, organization_id=org.id
    )
    job = await db.scalar(
        select(IntegrationJob).where(
            IntegrationJob.dedupe_key == notifications.first_handoff_key(
                org.id, session.id
            )
        )
    )
    with pytest.raises(JobDeferredError, match="configuration missing"):
        await notifications.process_handoff_job(db, job)
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_reconciliation_finds_missing_job_once(db, client, org_scope, monkeypatch):
    _enable(monkeypatch)
    org, session = await _session(db, client, org_scope)
    # Model a previous crash / missed event path, not a customer action.
    session.status = "human_requested"
    session.updated_at = datetime.now(UTC)
    await db.commit()
    staged = await notifications.reconcile_missing_handoffs(db)
    again = await notifications.reconcile_missing_handoffs(db)
    assert len(staged) == 1
    assert again == [staged[0]]
    key = notifications.first_handoff_key(org.id, session.id)
    rows = (
        await db.execute(
            select(IntegrationJob.id).where(IntegrationJob.dedupe_key == key)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_atomic_rollback_when_outbox_staging_fails(db, client, org_scope, monkeypatch):
    """A failed enqueue cannot commit a human_requested status without an outbox.

    The proof does not rely on the failing session's own (rolled back) state:
    it reads through an independent connection.
    """
    from app.core.database import AsyncSessionLocal

    _enable(monkeypatch)
    org, session = await _session(db, client, org_scope)
    # Stable primary keys, captured before the rollback expires the ORM objects.
    session_id, org_id = session.id, org.id

    async def fail_stage(_db, *, organization_id, session_id):
        raise RuntimeError("deliberate synthetic insert failure")

    monkeypatch.setattr(notifications, "stage_initial_handoff", fail_stage)
    with pytest.raises(RuntimeError, match="deliberate synthetic"):
        await PublicChatRepository.handoff_to_human(
            db, session=session, organization_id=org_id
        )

    async with AsyncSessionLocal() as other:  # a different connection
        status = await other.scalar(
            select(PublicChatSession.status).where(PublicChatSession.id == session_id)
        )
        jobs = (
            await other.execute(
                select(IntegrationJob.id).where(
                    IntegrationJob.organization_id == org_id,
                    IntegrationJob.job_type == notifications.NOTIFY_JOB_TYPE,
                )
            )
        ).scalars().all()
    assert status == "ai_active"
    assert jobs == []
    # the repository rolled back, so the caller's session is immediately usable
    assert (
        await db.scalar(
            select(PublicChatSession.status).where(PublicChatSession.id == session_id)
        )
        == "ai_active"
    )


@pytest.mark.asyncio
async def test_flood_cap_suppresses_excess_and_creates_one_digest(
    db, client, org_scope, monkeypatch
):
    """Eight real tenant handoffs yield six attempts per channel and one digest."""
    from app.repositories.integration_job_repository import IntegrationJobRepository
    from app.services import apprise_sender
    from app.services.integration_job_service import IntegrationJobService
    from app.services.public_chat_turn_dispatcher import handoff_dispatcher

    _enable(monkeypatch)
    monkeypatch.setenv("HANDOFF_NOTIFICATIONS_ENABLED", "true")
    # Production HTTPS validation stays on; the disposable environment simply
    # provides an HTTPS frontend origin.
    monkeypatch.setattr(
        notifications.settings, "frontend_base_url", "https://staff.example.test"
    )
    monkeypatch.setattr(handoff_dispatcher, "submit", lambda *_a: True)
    monkeypatch.setattr(
        notifications.settings,
        "handoff_notification_email_url",
        "mailtos://user:secret@company.example?smtp=smtp.gmail.com&to=staff@example.com",
    )
    monkeypatch.setattr(
        notifications.settings,
        "handoff_notification_telegram_url",
        "tgram://123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef/123456789",
    )
    sent: list[str] = []
    async def fake_send(*, channel, configured, title, body):
        assert "CXOps AI" in title and body.startswith("CXOps AI")
        assert "secret" not in body and "customer message" not in body
        assert body.endswith("https://staff.example.test/public-chat")
        sent.append(channel)
    monkeypatch.setattr(apprise_sender, "send_staff_handoff", fake_send)

    org = await org_scope()
    org.external_id = "rispu"
    await db.commit()
    _cfg, key = await base._make_widget_config(db, org)
    sessions = []
    for _ in range(8):
        await base._create_session(client, key)
    rows = await db.execute(
        select(PublicChatSession).where(
            PublicChatSession.organization_id == org.id
        ).order_by(PublicChatSession.id.asc())
    )
    sessions = list(rows.scalars().all())
    assert len(sessions) == 8
    for session in sessions:
        await PublicChatRepository.handoff_to_human(
            db, session=session, organization_id=org.id
        )
    for session in sessions:
        job = await db.scalar(
            select(IntegrationJob).where(
                IntegrationJob.dedupe_key == notifications.first_handoff_key(
                    org.id, session.id
                )
            )
        )
        assert job is not None
        claimed = await IntegrationJobRepository.claim_by_id(
            db, job_id=job.id,
            job_type=notifications.NOTIFY_JOB_TYPE,
            stale_after_seconds=120,
        )
        assert claimed is not None
        await IntegrationJobService.execute(db, claimed)
        completed = await IntegrationJobRepository.mark_completed(
            db, job_id=claimed.id, claimed_at=claimed.locked_at
        )
        assert completed is not None and completed.status == "completed"
    assert sent.count("email") == 6 and sent.count("telegram") == 6
    results = await db.execute(
        select(IntegrationJob).where(
            IntegrationJob.organization_id == org.id,
            IntegrationJob.job_type == notifications.NOTIFY_JOB_TYPE,
        )
    )
    all_jobs = list(results.scalars().all())
    suppressed = [
        j for j in all_jobs
        if j.payload.get("outcome") == "suppressed:rate_limited"
    ]
    digests = [j for j in all_jobs if j.payload.get("event") == "digest"]
    assert len(suppressed) == 2
    assert len(digests) == 1
    assert digests[0].status == "pending"
    # exactly six admitted per hour: the other two never reached a provider,
    # and the digest is not due (nor sent) inside the hour it summarises
    assert sorted(j.payload.get("outcome") for j in all_jobs if j.payload.get("event") == "initial").count("provider_accepted") == 6
    assert sent.count("email") + sent.count("telegram") == 12
    assert digests[0].available_at > datetime.now(UTC)
