"""Phase 1U-A hardening: handoff paths, transport security, partial retries,
flood control across workers, recovery, dispatcher bounds and inactivity.

Disposable PostgreSQL; every provider is a fake or a mocked socket layer.
No real e-mail, Telegram or Apprise network call is ever made.
"""
from __future__ import annotations

import asyncio
import io
import logging
import secrets
import smtplib
import subprocess
import sys
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from sqlalchemy import select, update

from app.core.database import AsyncSessionLocal
from app.models.conversation import Conversation
from app.models.integration_job import IntegrationJob
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.models.ticket import Ticket
from app.repositories.integration_job_repository import IntegrationJobRepository
from app.repositories.public_chat_repository import PublicChatRepository
from app.services import apprise_sender
from app.services import handoff_notification_service as notifications
from app.services.apprise_sender import (
    NotificationDestinationError,
    NotificationTransportError,
    restricted_url,
    send_staff_handoff,
)
from app.services.delivery_lease import handoff_notification_delivery_lease
from app.services.integration_job_service import (
    IntegrationJobService,
)
from app.services.public_chat_service import PublicChatService, hash_digest
from app.services.public_chat_turn_dispatcher import TurnDispatcher, handoff_dispatcher
from tests import test_public_chat as base

REPO_ROOT = Path(__file__).resolve().parents[1]

_configure_auth = base._configure_auth
_phase1p_namespace_isolated = base._phase1p_namespace_isolated
client = base.client
db = base.db
org_scope = base.org_scope

# Random canaries generated per run (so no credential-shaped literal exists in
# the repository). The tests assert none of them ever reaches a log, an
# exception chain, a job payload or an outgoing message.
TOKEN = secrets.token_urlsafe(24)  # Telegram-token-shaped: [A-Za-z0-9_-]{32}
PASSWORD = "canary-" + secrets.token_hex(8)
TELEGRAM_URL = f"tgram://123456789:{TOKEN}/123456789"
EMAIL_URL = (
    f"mailtos://svc:{PASSWORD}@company.example"
    "?smtp=smtp.gmail.com&to=staff@example.com"
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _enable(monkeypatch, *, channels: str = "email,telegram") -> None:
    s = notifications.settings
    monkeypatch.setattr(s, "handoff_notifications_enabled", True)
    monkeypatch.setattr(s, "handoff_notification_activated_at", datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setattr(s, "handoff_notification_pilot_tenant", "rispu")
    monkeypatch.setattr(s, "handoff_notification_max_per_hour", 6)
    monkeypatch.setattr(s, "handoff_notification_channels", channels)
    monkeypatch.setattr(s, "handoff_notification_email_url", EMAIL_URL)
    monkeypatch.setattr(s, "handoff_notification_telegram_url", TELEGRAM_URL)
    monkeypatch.setattr(s, "frontend_base_url", "https://staff.example.test")
    monkeypatch.setenv("HANDOFF_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setattr(handoff_dispatcher, "submit", lambda *_a, **_k: True)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.delay = 0.0
        self.in_flight = 0
        self.peak = 0

    async def __call__(self, *, channel, configured, title, body):
        assert title.startswith("CXOps AI") and body.startswith("CXOps AI")
        assert TOKEN not in body and PASSWORD not in body
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if channel in self.fail:
                raise NotificationTransportError("notification provider did not accept")
            self.calls.append(channel)
        finally:
            self.in_flight -= 1


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(apprise_sender, "send_staff_handoff", fake)
    return fake


async def _rispu_org(db, org_scope):
    org = await org_scope()
    org.external_id = "rispu"
    await db.commit()
    return org


async def _make_sessions(db, org, count: int, *, status: str = "ai_active") -> list[int]:
    config = await db.scalar(
        select(PublicChatConfiguration).where(
            PublicChatConfiguration.organization_id == org.id
        )
    )
    if config is None:
        config = PublicChatConfiguration(
            organization_id=org.id,
            public_widget_key_hash=hash_digest(f"pk_live_{secrets.token_hex(16)}"),
            display_name="W", welcome_message="hi", allowed_origins=[], enabled=True,
            max_message_length=2000, max_messages_per_minute=20, session_ttl_hours=24,
        )
        db.add(config)
        await db.flush()
    ids = []
    for _ in range(count):
        conversation = Conversation(
            organization_id=org.id, provider="public_chat", channel="public_chat", status="open"
        )
        db.add(conversation)
        await db.flush()
        ticket = Ticket(
            organization_id=org.id, subject="Widget", description=".", status="new",
            priority="medium", source="public-chat-widget",
        )
        db.add(ticket)
        await db.flush()
        session = PublicChatSession(
            organization_id=org.id, configuration_id=config.id, conversation_id=conversation.id,
            ticket_id=ticket.id, token_hash=hash_digest(secrets.token_hex(32)), status=status,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        db.add(session)
        await db.flush()
        ids.append(session.id)
    await db.commit()
    return ids


async def _handoff(session_id: int, org_id: int) -> None:
    async with AsyncSessionLocal() as other:
        session = await other.get(PublicChatSession, session_id)
        await PublicChatRepository.handoff_to_human(other, session=session, organization_id=org_id)


async def _jobs(org_id: int) -> list[IntegrationJob]:
    async with AsyncSessionLocal() as other:
        rows = await other.execute(
            select(IntegrationJob)
            .where(
                IntegrationJob.organization_id == org_id,
                IntegrationJob.job_type == notifications.NOTIFY_JOB_TYPE,
            )
            .order_by(IntegrationJob.id)
        )
        return list(rows.scalars().all())


async def _make_due(job_id: int) -> None:
    """Let a deferral / retry back-off elapse."""
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == job_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()


async def _run_job(job_id: int) -> str:
    """Claim one job and process it through the REAL scheduled-worker path
    (``scripts.worker.process_claimed_job``: fenced claim, defer, retry, fail).

    Returns ``completed`` / ``retry`` / ``failed`` / ``deferred`` / ``claim_lost``
    (or ``unclaimed`` when the job is not claimable right now).
    """
    from scripts.worker import process_claimed_job

    async with AsyncSessionLocal() as worker:
        claimed = await IntegrationJobRepository.claim_by_id(
            worker, job_id=job_id, job_type=notifications.NOTIFY_JOB_TYPE, stale_after_seconds=120
        )
        if claimed is None:
            return "unclaimed"
        return (await process_claimed_job(worker, claimed)).outcome


# --------------------------------------------------------------------------
# B1 - atomic handoff on every path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_four_handoff_paths_stage_exactly_one_job(db, client, org_scope, monkeypatch):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    ids = await _make_sessions(db, org, 4)
    org_id = org.id

    # 1. explicit human request
    async with AsyncSessionLocal() as s1:
        session = await s1.get(PublicChatSession, ids[0])
        await PublicChatService.request_human(s1, session)

    # 2. AI / RAG / grounding fallback and 3. exhausted allowance share the AI
    #    turn's handoff site; both are driven through the real turn function.
    from app.schemas.agent import AgentDecision
    from app.services.agent_workflow_service import agent_workflow_service
    from app.services.knowledge_search_service import KnowledgeSearchService

    async def no_sources(*_a, **_k):
        return []

    class ReviewModel:
        async def ainvoke(self, _m):
            decision = AgentDecision(
                action="human_review", reason="needs a person",
                requires_human_approval=True, response_draft="",
            )
            return {"raw": types.SimpleNamespace(usage_metadata=None), "parsed": decision,
                    "parsing_error": None}

    monkeypatch.setattr(KnowledgeSearchService, "search", no_sources)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", ReviewModel())
    async with AsyncSessionLocal() as s2:
        session = await s2.get(PublicChatSession, ids[1])
        conversation = await s2.get(Conversation, session.conversation_id)
        inbound = await PublicChatService._insert_inbound_message(
            s2, conversation=conversation, text="I need a person please", client_message_id="m-ai"
        )
        configuration = await s2.get(PublicChatConfiguration, session.configuration_id)
        await PublicChatService._ai_reply_turn(
            s2, session=session, configuration=configuration, conversation=conversation,
            inbound=inbound, text="I need a person please", client_message_id="m-ai",
            timer=types.SimpleNamespace(mark=lambda *_a: None),
        )

    # 3. allowance exhausted: zero monthly turns hands off without a model call
    async with AsyncSessionLocal() as s3:
        session = await s3.get(PublicChatSession, ids[2])
        conversation = await s3.get(Conversation, session.conversation_id)
        configuration = await s3.get(PublicChatConfiguration, session.configuration_id)
        configuration.monthly_ai_turn_limit = 0
        await s3.commit()
        model_calls = {"n": 0}

        class CountingModel(ReviewModel):
            async def ainvoke(self, m):
                model_calls["n"] += 1
                return await super().ainvoke(m)

        monkeypatch.setattr(agent_workflow_service, "decision_llm", CountingModel())
        inbound = await PublicChatService._insert_inbound_message(
            s3, conversation=conversation, text="hello there", client_message_id="m-allow"
        )
        await PublicChatService._ai_reply_turn(
            s3, session=session, configuration=configuration, conversation=conversation,
            inbound=inbound, text="hello there", client_message_id="m-allow",
            timer=types.SimpleNamespace(mark=lambda *_a: None),
        )
        assert model_calls["n"] == 0

    # 4. permanently failed asynchronous turn
    async with AsyncSessionLocal() as s4:
        await PublicChatService.fail_turn_to_handoff(
            s4, organization_id=org_id, session_id=ids[3], client_message_id="m-fail"
        )

    jobs = await _jobs(org_id)
    assert sorted(j.dedupe_key for j in jobs) == sorted(
        notifications.first_handoff_key(org_id, sid) for sid in ids
    )
    async with AsyncSessionLocal() as check:
        statuses = (
            await check.execute(
                select(PublicChatSession.status).where(PublicChatSession.id.in_(ids))
            )
        ).scalars().all()
    assert set(statuses) == {"human_requested"}


@pytest.mark.asyncio
async def test_concurrent_handoffs_for_one_session_create_one_job(db, client, org_scope, monkeypatch):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await asyncio.gather(*(_handoff(sid, org.id) for _ in range(8)))
    jobs = await _jobs(org.id)
    assert len(jobs) == 1 and jobs[0].dedupe_key == notifications.first_handoff_key(org.id, sid)


@pytest.mark.asyncio
async def test_assigned_closed_and_foreign_sessions_never_notify_or_change(
    db, client, org_scope, monkeypatch
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (assigned,) = await _make_sessions(db, org, 1, status="human_assigned")
    (closed,) = await _make_sessions(db, org, 1, status="closed")
    for sid in (assigned, closed):
        await _handoff(sid, org.id)
    async with AsyncSessionLocal() as check:
        rows = dict(
            (await check.execute(
                select(PublicChatSession.id, PublicChatSession.status).where(
                    PublicChatSession.id.in_([assigned, closed])
                )
            )).all()
        )
    assert rows == {assigned: "human_assigned", closed: "closed"}  # never returned to AI
    assert await _jobs(org.id) == []

    other = await org_scope()
    (foreign,) = await _make_sessions(db, other, 1)
    with pytest.raises(ValueError, match="does not belong"):
        async with AsyncSessionLocal() as s:
            session = await s.get(PublicChatSession, foreign)
            await PublicChatRepository.handoff_to_human(s, session=session, organization_id=org.id)
    assert await _jobs(org.id) == [] and await _jobs(other.id) == []


@pytest.mark.asyncio
async def test_flag_off_preserves_the_original_handoff_and_stages_nothing(
    db, client, org_scope, monkeypatch
):
    monkeypatch.setattr(notifications.settings, "handoff_notifications_enabled", False)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)
    async with AsyncSessionLocal() as check:
        assert await check.scalar(
            select(PublicChatSession.status).where(PublicChatSession.id == sid)
        ) == "human_requested"
    assert await _jobs(org.id) == []


@pytest.mark.asyncio
async def test_not_yet_active_rollout_never_breaks_a_handoff(db, client, org_scope, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        notifications.settings, "handoff_notification_activated_at",
        datetime.now(UTC) + timedelta(days=1),
    )
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)  # must not raise
    async with AsyncSessionLocal() as check:
        assert await check.scalar(
            select(PublicChatSession.status).where(PublicChatSession.id == sid)
        ) == "human_requested"
    assert await _jobs(org.id) == []


def test_invalid_notification_settings_downgrade_instead_of_raising():
    from app.core.config import Settings

    for kwargs in (
        {"handoff_notifications_enabled": True},  # no activation time
        {"handoff_notifications_enabled": True,
         "handoff_notification_activated_at": datetime.now(UTC),
         "handoff_notification_pilot_tenant": "someone-else"},
        {"handoff_notifications_enabled": True,
         "handoff_notification_activated_at": datetime.now(UTC),
         "handoff_notification_max_per_hour": 60},
        {"handoff_notifications_enabled": True,
         "handoff_notification_activated_at": datetime.now(UTC),
         "handoff_notification_channels": "email,sms"},
    ):
        cfg = Settings(**kwargs)
        assert cfg.handoff_notifications_enabled is False
        assert cfg.handoff_notification_config_error


# --------------------------------------------------------------------------
# B2 - the real Apprise 2.0.1 behind the wrapper (mocked sockets only)
# --------------------------------------------------------------------------

apprise = pytest.importorskip("apprise")


def test_apprise_is_the_reviewed_version():
    assert apprise.__version__ == "2.0.1"


@pytest.mark.parametrize(
    "to",
    [
        "staff@example.com,other",
        "staff@example.com;other@example.com",
        "staff@example.com other@example.com",
        "staff%40example.com%2Cother%40example.com",
        "Staff%20%3Cstaff%40example.com%3E",
        "staff@example.com%0D%0ABcc:evil@example.com",
        "",
    ],
)
def test_email_recipient_list_or_header_injection_is_rejected(to):
    url = f"mailtos://svc:pw@company.example?smtp=smtp.gmail.com&to={to}"
    with pytest.raises(NotificationDestinationError):
        restricted_url("email", url)


@pytest.mark.parametrize(
    "extra",
    ["&cc=b@example.com", "&bcc=c@example.com", "&attach=file:///etc/passwd", "&verify=no",
     "&mode=none", "&format=html", "&to=b@example.com", "&user=x", "&pass=y", "&name=z",
     "&smtp=smtp.sendgrid.net", "&include=http://evil.test"],
)
def test_email_options_beyond_the_allowlist_are_rejected(extra):
    with pytest.raises(NotificationDestinationError):
        restricted_url(
            "email", "mailtos://svc:pw@company.example?smtp=smtp.gmail.com&to=a@example.com" + extra
        )


def test_real_plugins_receive_forced_socket_timeouts_and_a_single_target():
    hostile = EMAIL_URL + "&cto=9999&rto=9999"
    for channel, url, plugin in (
        ("email", hostile, "NotifyEmail"),
        ("telegram", TELEGRAM_URL + "?cto=9999&rto=9999", "NotifyTelegram"),
    ):
        service = apprise.Apprise()
        assert service.add(restricted_url(channel, url))
        loaded = service[0]
        assert type(loaded).__name__ == plugin
        assert loaded.socket_connect_timeout == 2.0
        assert loaded.socket_read_timeout == 4.0
        assert len(loaded.targets) == 1


@pytest.mark.asyncio
async def test_telegram_request_is_fixed_host_tls_verified_and_time_bounded():
    seen: list[dict] = []

    class Response:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"ok": True}

    def fake_post(url, *a, **k):
        seen.append({"host": url.split("/")[2], "timeout": k.get("timeout"), "verify": k.get("verify")})
        return Response()

    with mock.patch("requests.post", fake_post), mock.patch(
        "requests.Session.request", lambda self, m, url, *a, **k: fake_post(url, *a, **k)
    ), mock.patch("requests.Session.post", lambda self, url, *a, **k: fake_post(url, *a, **k)):
        await send_staff_handoff(
            channel="telegram", configured=TELEGRAM_URL, title="CXOps AI — x", body="CXOps AI y"
        )
    assert seen and all(
        c["host"] == "api.telegram.org" and c["timeout"] == (2.0, 4.0) and c["verify"] is True
        for c in seen
    )


@pytest.mark.asyncio
async def test_email_uses_allowlisted_host_starttls_and_bounded_socket_timeout():
    events: list[tuple] = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            events.append(("connect", a[0], a[1], k.get("timeout")))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self, *a):
            pass

        def starttls(self, *a, **k):
            events.append(("starttls",))

        def login(self, *a):
            pass

        def sendmail(self, frm, to, msg):
            events.append(("sendmail", tuple(to)))

        def quit(self):
            pass

        def close(self):
            pass

    with mock.patch("smtplib.SMTP", FakeSMTP), mock.patch("smtplib.SMTP_SSL", FakeSMTP):
        await send_staff_handoff(
            channel="email", configured=EMAIL_URL, title="CXOps AI — x", body="CXOps AI y"
        )
    assert ("connect", "smtp.gmail.com", 587, 2.0) in events
    assert ("starttls",) in events
    assert ("sendmail", ("staff@example.com",)) in events


@pytest.mark.asyncio
async def test_apprise_never_leaks_credentials_into_logs_or_exceptions():
    import requests

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        def boom(*_a, **_k):
            raise requests.exceptions.ConnectionError(
                f"Max retries exceeded with url: https://api.telegram.org/bot123456789:{TOKEN}/sendMessage"
            )

        with (
            mock.patch("requests.post", boom),
            mock.patch("requests.Session.post", lambda *_a, **_k: boom()),
            mock.patch("requests.Session.request", lambda *_a, **_k: boom()),
            pytest.raises(NotificationTransportError) as tg_error,
        ):
            await send_staff_handoff(
                channel="telegram", configured=TELEGRAM_URL, title="CXOps AI — x", body="CXOps AI y"
            )

        class BadSMTP:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self, *a):
                pass

            def starttls(self, *a, **k):
                pass

            def login(self, user, pw):
                raise smtplib.SMTPAuthenticationError(535, f"bad credentials {pw}".encode())

            def quit(self):
                pass

            def close(self):
                pass

        with (
            mock.patch("smtplib.SMTP", BadSMTP),
            mock.patch("smtplib.SMTP_SSL", BadSMTP),
            pytest.raises(NotificationTransportError) as mail_error,
        ):
            await send_staff_handoff(
                channel="email", configured=EMAIL_URL, title="CXOps AI — x", body="CXOps AI y"
            )
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)

    captured = buffer.getvalue()
    for secret in (TOKEN, PASSWORD, "staff@example.com", "123456789"):
        assert secret not in captured
    for error in (tg_error.value, mail_error.value):
        text = repr(error) + repr(error.__cause__) + repr(error.__context__)
        assert TOKEN not in text and PASSWORD not in text
        assert error.__cause__ is None
        assert error.__context__ is None or error.__suppress_context__


def test_import_of_the_application_does_not_load_apprise():
    proc = subprocess.run(
        [sys.executable, "-c", "import sys, app.main; print('apprise' in sys.modules)"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    assert proc.stdout.strip() == "False"


# --------------------------------------------------------------------------
# B3 / C - partial retries, flood control, recovery, dispatcher
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_is_not_resent_when_telegram_fails_after_an_accepted_email(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)
    (job,) = await _jobs(org.id)

    provider.fail = {"telegram"}
    assert await _run_job(job.id) == "retry"
    assert provider.calls == ["email"]
    (stored,) = await _jobs(org.id)
    assert stored.payload["accepted_channels"] == ["email"]
    assert "outcome" not in stored.payload
    assert stored.status == "retry" and stored.attempts == 1

    provider.fail = set()
    async with AsyncSessionLocal() as s:  # make the backoff elapse
        await s.execute(update(IntegrationJob).where(IntegrationJob.id == job.id).values(
            available_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    assert await _run_job(job.id) == "completed"
    assert provider.calls == ["email", "telegram"]  # email exactly once
    (final,) = await _jobs(org.id)
    assert final.payload["outcome"] == "provider_accepted"
    assert final.payload["accepted_channels"] == ["email", "telegram"]
    assert set(final.payload) <= {
        "event", "session_id", "quota_granted", "accepted_channels", "outcome"
    }


@pytest.mark.asyncio
async def test_two_workers_on_one_job_never_send_in_parallel(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)
    (job,) = await _jobs(org.id)

    async with handoff_notification_delivery_lease(job.id) as held:
        assert held is True  # another worker is mid-delivery for this job
        assert await _run_job(job.id) == "deferred"
    assert provider.calls == []
    (after,) = await _jobs(org.id)
    assert after.attempts == 0  # deferral does not consume the attempt budget

    await _make_due(job.id)
    assert await _run_job(job.id) == "completed"
    assert provider.calls == ["email", "telegram"]


@pytest.mark.asyncio
async def test_worker_without_configuration_defers_and_keeps_the_attempt_budget(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)
    (job,) = await _jobs(org.id)

    monkeypatch.setenv("HANDOFF_NOTIFICATIONS_ENABLED", "")  # runner never got the variable
    for _ in range(4):
        assert await _run_job(job.id) == "deferred"
        async with AsyncSessionLocal() as s:
            await s.execute(update(IntegrationJob).where(IntegrationJob.id == job.id).values(
                available_at=datetime.now(UTC) - timedelta(seconds=1)))
            await s.commit()
    (still,) = await _jobs(org.id)
    assert still.attempts == 0 and still.status == "retry"
    assert "outcome" not in still.payload
    assert provider.calls == []

    # an explicit OFF is a different, deliberate state: it suppresses, once
    monkeypatch.setenv("HANDOFF_NOTIFICATIONS_ENABLED", "false")
    assert await _run_job(job.id) == "completed"
    (off,) = await _jobs(org.id)
    assert off.payload["outcome"] == "suppressed:disabled"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_a_runner_missing_one_required_channel_defers_without_a_partial_send(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)
    (job,) = await _jobs(org.id)
    monkeypatch.setattr(notifications.settings, "handoff_notification_telegram_url", "")
    assert await _run_job(job.id) == "deferred"
    assert provider.calls == []  # email was NOT sent alone
    await _make_due(job.id)

    # a deployment that deliberately uses e-mail only needs no Telegram secret
    monkeypatch.setattr(notifications.settings, "handoff_notification_channels", "email")
    assert await _run_job(job.id) == "completed"
    assert provider.calls == ["email"]


@pytest.mark.asyncio
async def test_120_handoffs_in_an_hour_send_six_per_channel_and_one_digest_across_workers(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    ids = await _make_sessions(db, org, 120)
    for sid in ids:
        await _handoff(sid, org.id)
    jobs = await _jobs(org.id)
    assert len(jobs) == 120

    # several concurrent "workers", each with its own database session
    sem = asyncio.Semaphore(6)

    async def worker(job_id: int) -> str:
        async with sem:
            return await _run_job(job_id)

    results = await asyncio.gather(*(worker(j.id) for j in jobs))
    assert set(results) == {"completed"}

    assert provider.calls.count("email") == 6
    assert provider.calls.count("telegram") == 6  # 12 provider calls for 120 handoffs
    final = await _jobs(org.id)
    initial = [j for j in final if j.payload["event"] == "initial"]
    digests = [j for j in final if j.payload["event"] == "digest"]
    accepted = [j for j in initial if j.payload.get("outcome") == "provider_accepted"]
    suppressed = [j for j in initial if j.payload.get("outcome") == "suppressed:rate_limited"]
    assert len(accepted) == 6 and len(suppressed) == 114
    assert len(digests) == 1 and digests[0].status == "pending"
    assert digests[0].available_at > datetime.now(UTC)  # never immediate
    assert {j.payload["digest_hour"] for j in suppressed} == {
        digests[0].payload["hour_start"]
    }

    # time passes: the summarised hour is over and the digest becomes due
    previous = (datetime.now(UTC) - timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    )
    async with AsyncSessionLocal() as s:
        for j in suppressed:
            await s.execute(update(IntegrationJob).where(IntegrationJob.id == j.id).values(
                payload={**j.payload, "digest_hour": previous.isoformat()}))
        await s.execute(update(IntegrationJob).where(IntegrationJob.id == digests[0].id).values(
            dedupe_key=notifications.digest_key(org.id, previous),
            payload={"event": "digest", "hour_start": previous.isoformat()},
            available_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()

    bodies: list[str] = []
    original = provider.__call__

    async def capture(*, channel, configured, title, body):
        bodies.append(body)
        await original(channel=channel, configured=configured, title=title, body=body)

    monkeypatch.setattr(apprise_sender, "send_staff_handoff", capture)
    assert await _run_job(digests[0].id) == "completed"
    assert len(bodies) == 2 and all("114 additional handoff requests" in b for b in bodies)
    assert all(b.endswith("https://staff.example.test/public-chat") for b in bodies)
    assert provider.calls.count("email") == 7 and provider.calls.count("telegram") == 7


@pytest.mark.asyncio
async def test_retries_of_an_admitted_job_do_not_consume_more_quota(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    monkeypatch.setattr(notifications.settings, "handoff_notification_max_per_hour", 1)
    org = await _rispu_org(db, org_scope)
    first, second = await _make_sessions(db, org, 2)
    await _handoff(first, org.id)
    jobs = await _jobs(org.id)
    provider.fail = {"email"}
    assert await _run_job(jobs[0].id) == "retry"
    provider.fail = set()
    async with AsyncSessionLocal() as s:
        await s.execute(update(IntegrationJob).where(IntegrationJob.id == jobs[0].id).values(
            available_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    await _handoff(second, org.id)  # second handoff arrives while the first retries
    other = next(j for j in await _jobs(org.id) if j.id != jobs[0].id)
    assert await _run_job(other.id) == "completed"  # over the cap of 1: suppressed
    assert await _run_job(jobs[0].id) == "completed"  # the admitted one still sends
    assert provider.calls == ["email", "telegram"]
    final = {j.id: j for j in await _jobs(org.id)}
    assert final[jobs[0].id].payload["outcome"] == "provider_accepted"
    assert final[other.id].payload["outcome"] == "suppressed:rate_limited"


@pytest.mark.asyncio
async def test_stale_resolved_or_foreign_work_is_suppressed_without_a_send(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    ids = await _make_sessions(db, org, 3)
    for sid in ids:
        await _handoff(sid, org.id)
    jobs = {j.dedupe_key: j for j in await _jobs(org.id)}
    async with AsyncSessionLocal() as s:
        await s.execute(update(PublicChatSession).where(PublicChatSession.id == ids[0]).values(
            status="closed"))
        await s.execute(update(PublicChatSession).where(PublicChatSession.id == ids[1]).values(
            status="human_assigned"))
        await s.commit()
    for sid in ids[:2]:
        key = notifications.first_handoff_key(org.id, sid)
        assert await _run_job(jobs[key].id) == "completed"
    outcomes = [j.payload.get("outcome") for j in await _jobs(org.id)]
    assert outcomes.count("suppressed:stale") == 2
    assert provider.calls == []

    # a job whose persisted tenant disagrees with the session's tenant never sends
    other = await org_scope()
    other.external_id = "someone-else"
    await db.commit()
    forged = IntegrationJob(
        dedupe_key=notifications.first_handoff_key(other.id, ids[2]),
        organization_id=other.id, job_type=notifications.NOTIFY_JOB_TYPE,
        payload={"event": "initial", "session_id": ids[2]}, status="pending", max_attempts=3,
    )
    db.add(forged)
    await db.commit()
    assert await _run_job(forged.id) == "completed"
    async with AsyncSessionLocal() as check:
        row = await check.get(IntegrationJob, forged.id)
    assert row.payload["outcome"] == "suppressed:inactive"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_malformed_or_content_bearing_payloads_are_permanent_failures(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    bad = IntegrationJob(
        dedupe_key=notifications.first_handoff_key(org.id, sid), organization_id=org.id,
        job_type=notifications.NOTIFY_JOB_TYPE, max_attempts=3, status="pending",
        payload={"event": "initial", "session_id": sid, "message": "customer text", "url": "http://x"},
    )
    db.add(bad)
    await db.commit()
    assert await _run_job(bad.id) == "failed"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_reconciliation_after_a_crash_stages_only_post_activation_unreleased_work(
    db, client, org_scope, monkeypatch
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    fresh, old, released = await _make_sessions(db, org, 3, status="human_requested")
    async with AsyncSessionLocal() as s:
        await s.execute(update(PublicChatSession).where(PublicChatSession.id == old).values(
            updated_at=datetime.now(UTC) - timedelta(days=3)))
        await s.execute(update(PublicChatSession).where(PublicChatSession.id == released).values(
            released_at=datetime.now(UTC)))
        await s.commit()
    async with AsyncSessionLocal() as s:
        staged = await notifications.reconcile_missing_handoffs(s)
    assert [(jid) for jid, _org in staged] and len(staged) == 1
    keys = [j.dedupe_key for j in await _jobs(org.id)]
    assert keys == [notifications.first_handoff_key(org.id, fresh)]


# --- dispatcher: bounded, cleaned up, inactive when OFF ---------------------


async def _insert_jobs(org_ids: list[int]) -> list[tuple[int, int]]:
    out = []
    async with AsyncSessionLocal() as s:
        for index, org_id in enumerate(org_ids):
            job = IntegrationJob(
                dedupe_key=f"handoff-notify:v1:{org_id}:{900000 + index}:initial",
                organization_id=org_id, job_type=notifications.NOTIFY_JOB_TYPE,
                payload={"event": "initial", "session_id": 900000 + index},
                status="pending", max_attempts=3,
            )
            s.add(job)
            await s.flush()
            out.append((job.id, org_id))
        await s.commit()
    return out


@pytest.mark.asyncio
async def test_dispatcher_enforces_global_two_and_per_tenant_one(db, org_scope, monkeypatch):
    orgs = [await org_scope() for _ in range(3)]
    jobs = await _insert_jobs([orgs[0].id, orgs[0].id, orgs[1].id, orgs[1].id, orgs[2].id])
    state = {"now": 0, "peak": 0, "per": {}, "peak_per": 0}

    async def slow_execute(*_a, **_k):
        job = _k.get("job") or _a[-1]
        state["now"] += 1
        state["per"][job.organization_id] = state["per"].get(job.organization_id, 0) + 1
        state["peak"] = max(state["peak"], state["now"])
        state["peak_per"] = max(state["peak_per"], state["per"][job.organization_id])
        await asyncio.sleep(0.15)
        state["now"] -= 1
        state["per"][job.organization_id] -= 1

    monkeypatch.setattr(IntegrationJobService, "execute", staticmethod(slow_execute))
    dispatcher = TurnDispatcher(max_concurrency=2, per_tenant=1, job_type=notifications.NOTIFY_JOB_TYPE)
    for job_id, org_id in jobs:
        assert dispatcher.submit(job_id, org_id)
    await dispatcher.drain(timeout=10)
    assert state["peak"] == 2 and state["peak_per"] == 1
    async with AsyncSessionLocal() as s:
        statuses = (await s.execute(
            select(IntegrationJob.status).where(IntegrationJob.id.in_([j for j, _ in jobs]))
        )).scalars().all()
    assert set(statuses) == {"completed"}


@pytest.mark.asyncio
async def test_hung_provider_does_not_block_the_event_loop_and_drain_cleans_up(
    db, org_scope, monkeypatch
):
    org = await org_scope()
    jobs = await _insert_jobs([org.id])

    async def hang(*_a, **_k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(IntegrationJobService, "execute", staticmethod(hang))
    dispatcher = TurnDispatcher(max_concurrency=2, per_tenant=1, job_type=notifications.NOTIFY_JOB_TYPE)
    dispatcher.submit(*jobs[0])

    worst = 0.0
    started = time.perf_counter()
    while time.perf_counter() - started < 0.4:  # the API loop keeps ticking
        tick = time.perf_counter()
        await asyncio.sleep(0.02)
        worst = max(worst, time.perf_counter() - tick - 0.02)
    assert worst < 0.25

    await dispatcher.shutdown(timeout=0.3)
    assert len(dispatcher._inflight) == 0  # no leaked task after shutdown
    async with AsyncSessionLocal() as s:
        row = await s.get(IntegrationJob, jobs[0][0])
    assert row.status == "processing"  # recoverable by stale-claim recovery


@pytest.mark.asyncio
async def test_submit_is_bounded(monkeypatch):
    dispatcher = TurnDispatcher(max_concurrency=2, per_tenant=1, job_type=notifications.NOTIFY_JOB_TYPE)
    monkeypatch.setattr(dispatcher, "_run", lambda *_a: asyncio.sleep(3600))
    accepted = [dispatcher.submit(i, 1) for i in range(1, 100)]
    assert accepted.count(True) == 32  # bounded pending work; the rest stay durable
    await dispatcher.shutdown(timeout=0.2)


@pytest.mark.asyncio
async def test_lifespan_with_the_flag_off_starts_no_notification_machinery(monkeypatch):
    from app import main as main_module
    from app.core import schema_gate
    from app.services import provider_warmup

    async def ok(_engine):
        return None

    async def nothing():
        return None

    monkeypatch.setattr(schema_gate, "check_schema", ok)
    monkeypatch.setattr(provider_warmup, "warm_provider_clients", nothing)
    monkeypatch.setattr(main_module.settings, "handoff_notifications_enabled", False)
    called = {"n": 0}
    monkeypatch.setattr(
        handoff_dispatcher, "run_maintenance",
        lambda: called.__setitem__("n", called["n"] + 1) or asyncio.sleep(0),
    )
    async with main_module.lifespan(main_module.app):
        pass
    assert called["n"] == 0
    assert len(handoff_dispatcher._inflight) == 0


def test_analysis_capacity_reserves_connections_for_notification_leases_only_when_enabled(
    monkeypatch,
):
    from app.core import database
    from app.core.config import settings
    from app.core.db_connection import AnalysisCapacity

    monkeypatch.setattr(database, "engine", types.SimpleNamespace(
        sync_engine=types.SimpleNamespace(
            pool=types.SimpleNamespace(size=lambda: 5, _max_overflow=10)
        )
    ))
    monkeypatch.setattr(settings, "public_chat_turn_max_concurrency", 6)
    capacity = AnalysisCapacity(configured=50)
    monkeypatch.setattr(settings, "handoff_notifications_enabled", False)
    off = capacity.effective_limit()
    monkeypatch.setattr(settings, "handoff_notifications_enabled", True)
    on = capacity.effective_limit()
    assert off == 15 - 6 - 2
    assert on == off - 2


@pytest.mark.asyncio
async def test_an_invalid_destination_defers_the_alert_instead_of_consuming_it(
    db, client, org_scope, monkeypatch, provider
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)
    (job,) = await _jobs(org.id)
    monkeypatch.setattr(
        notifications.settings, "handoff_notification_email_url", "json://evil.example/hook"
    )
    assert await _run_job(job.id) == "deferred"
    (still,) = await _jobs(org.id)
    assert still.attempts == 0 and "outcome" not in still.payload
    assert provider.calls == []

    monkeypatch.setattr(notifications.settings, "handoff_notification_email_url", EMAIL_URL)
    await _make_due(job.id)
    assert await _run_job(job.id) == "completed"  # delivered once corrected
    assert provider.calls == ["email", "telegram"]


def test_unset_github_variables_mean_defaults_not_a_broken_process():
    from app.core.config import Settings

    cfg = Settings(
        handoff_notifications_enabled="",
        handoff_notification_channels="",
        handoff_notification_max_per_hour="",
        handoff_notification_pilot_tenant="",
        handoff_notification_activated_at="",
    )
    assert cfg.handoff_notifications_enabled is False
    assert cfg.handoff_notification_channels == "email,telegram"
    assert cfg.handoff_notification_max_per_hour == 6
    assert cfg.handoff_notification_pilot_tenant == "rispu"


def test_the_scheduled_worker_installs_the_lock_and_passes_every_runtime_variable():
    workflow = (REPO_ROOT / ".github" / "workflows" / "production-worker.yml").read_text()
    assert "pip install -r requirements-lock.txt" in workflow
    assert "cache-dependency-path: requirements-lock.txt" in workflow
    for name in (
        "HANDOFF_NOTIFICATIONS_ENABLED", "HANDOFF_NOTIFICATION_ACTIVATED_AT",
        "HANDOFF_NOTIFICATION_CHANNELS", "HANDOFF_NOTIFICATION_MAX_PER_HOUR",
        "HANDOFF_NOTIFICATION_EMAIL_URL", "HANDOFF_NOTIFICATION_TELEGRAM_URL",
    ):
        assert f"{name}: ${{{{" in workflow, name
    # secrets stay secrets: destinations come from `secrets.`, flags from `vars.`
    assert "secrets.HANDOFF_NOTIFICATION_EMAIL_URL" in workflow
    assert "secrets.HANDOFF_NOTIFICATION_TELEGRAM_URL" in workflow
    lock = (REPO_ROOT / "requirements-lock.txt").read_text()
    assert "apprise==2.0.1" in lock


@pytest.mark.asyncio
async def test_a_failed_dispatch_after_commit_leaves_a_durable_job_the_worker_completes(
    db, client, org_scope, monkeypatch, provider
):
    """The API commits the handoff, then dies/fails before submitting the job."""
    _enable(monkeypatch)

    def dispatcher_down(*_a, **_k):
        raise RuntimeError("event loop is gone")

    monkeypatch.setattr(handoff_dispatcher, "submit", dispatcher_down)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1)
    await _handoff(sid, org.id)  # the customer's handoff is unaffected
    (job,) = await _jobs(org.id)
    assert job.status == "pending"
    assert await _run_job(job.id) == "completed"  # scheduled-worker recovery
    assert provider.calls == ["email", "telegram"]


@pytest.mark.asyncio
async def test_api_startup_recovery_resubmits_pending_jobs_without_polling(
    db, client, org_scope, monkeypatch
):
    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    ids = await _make_sessions(db, org, 2)
    for sid in ids:
        await _handoff(sid, org.id)
    submitted: list[int] = []
    dispatcher = TurnDispatcher(max_concurrency=2, per_tenant=1, job_type=notifications.NOTIFY_JOB_TYPE)
    monkeypatch.setattr(
        dispatcher, "submit",
        lambda job_id, _org, delay=0.0: submitted.append(job_id) or True,
    )
    recovered = await dispatcher.recover_on_startup()
    assert recovered >= 2
    # (submit() is idempotent per in-flight job, so a job offered by both the
    # reconciler and the open-job scan still runs once)
    assert set(submitted) == {j.id for j in await _jobs(org.id)}


@pytest.mark.asyncio
async def test_staff_release_returns_to_the_queue_without_a_new_alert(
    db, client, org_scope, monkeypatch
):
    from app.services.public_chat_staff_service import PublicChatStaffService

    _enable(monkeypatch)
    org = await _rispu_org(db, org_scope)
    (sid,) = await _make_sessions(db, org, 1, status="human_assigned")
    async with AsyncSessionLocal() as s:
        await s.execute(update(PublicChatSession).where(PublicChatSession.id == sid).values(
            assigned_to_subject="staff-1", assigned_at=datetime.now(UTC)))
        await s.commit()
    async with AsyncSessionLocal() as s:
        await PublicChatStaffService.release(s, session_id=sid, organization_id=org.id)
    async with AsyncSessionLocal() as check:
        status = await check.scalar(
            select(PublicChatSession.status).where(PublicChatSession.id == sid)
        )
    assert status == "human_requested"
    assert await _jobs(org.id) == []  # re-queue is not an initial handoff
    async with AsyncSessionLocal() as s:
        assert await notifications.reconcile_missing_handoffs(s) == []  # nor repaired
