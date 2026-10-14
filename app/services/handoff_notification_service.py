"""Tenant-safe outbox for RISPU's feature-disabled staff notifications.

Initial handoff produces ONE job with the immutable key
handoff-notify:v1:{org}:{session}:initial. Email and Telegram are separately
tracked in bounded job payload metadata; NO URL, customer text, email address
or API credential ever enters that payload.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db_connection import release_idle_connection
from app.core.logging import get_logger
from app.core.metrics import record_handoff_notification
from app.models.integration_job import IntegrationJob
from app.models.organization import LIFECYCLE_ACTIVE, Organization
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.services.public_chat_rate_limiter import public_chat_rate_limiter_db

log = get_logger(__name__)
NOTIFY_JOB_TYPE = "handoff.notify"
CHANNELS = ("email", "telegram")  # every channel this service knows how to send


def required_channels() -> tuple[str, ...]:
    """Channels this deployment is configured to send; every runner needs all."""
    wanted = {c.strip() for c in settings.handoff_notification_channels.split(",")}
    return tuple(c for c in CHANNELS if c in wanted)
MAX_RECOVERY_BATCH = 50


def first_handoff_key(org: int, session: int) -> str:
    return f"handoff-notify:v1:{org}:{session}:initial"


def digest_key(org: int, hour: datetime) -> str:
    return f"handoff-digest:v1:{org}:{hour.strftime('%Y%m%d%H')}"


def activated_at() -> datetime | None:
    candidate = settings.handoff_notification_activated_at
    if candidate is None or candidate.tzinfo is None:
        return None
    return candidate.astimezone(UTC)


async def _correct_tenant(db: AsyncSession, org: int) -> bool:
    return await db.scalar(
        select(Organization.id).where(
            Organization.id == org,
            Organization.external_id == settings.handoff_notification_pilot_tenant,
            Organization.lifecycle_status == LIFECYCLE_ACTIVE,
        )
    ) is not None


async def stage_initial_handoff(
    db: AsyncSession, *, organization_id: int, session_id: int
) -> int | None:
    """Insert in the caller's open transaction, never commit the handoff here."""
    activation = activated_at()
    if not settings.handoff_notifications_enabled:
        return None
    if activation is None or activation > datetime.now(UTC):
        # Not yet active: the handoff itself must still succeed, unchanged.
        return None
    if not await _correct_tenant(db, organization_id):
        return None
    key = first_handoff_key(organization_id, session_id)
    stmt = (
        pg_insert(IntegrationJob)
        .values(
            dedupe_key=key,
            organization_id=organization_id,
            job_type=NOTIFY_JOB_TYPE,
            payload={"event": "initial", "session_id": session_id},
            status="pending",
            attempts=0,
            max_attempts=3,
        )
        .on_conflict_do_nothing(index_elements=[IntegrationJob.dedupe_key])
        .returning(IntegrationJob.id)
    )
    fresh = (await db.execute(stmt)).scalar_one_or_none()
    if fresh is not None:
        record_handoff_notification(outcome="staged")
        return int(fresh)
    return await db.scalar(
        select(IntegrationJob.id).where(
            IntegrationJob.dedupe_key == key,
            IntegrationJob.organization_id == organization_id,
            IntegrationJob.job_type == NOTIFY_JOB_TYPE,
        )
    )


async def reconcile_missing_handoffs(db: AsyncSession, *, limit: int = MAX_RECOVERY_BATCH) -> list[tuple[int, int]]:
    """Repair missed initial handoff jobs, excluding pre-activation history.

    Called only during startup/hourly maintenance, never by customer GETs.
    A released human-assigned session is not a new initial handoff.
    """
    since = activated_at()
    if not settings.handoff_notifications_enabled or since is None:
        return []
    if not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError("invalid reconciliation limit")
    org = await db.scalar(
        select(Organization.id).where(
            Organization.external_id == settings.handoff_notification_pilot_tenant,
            Organization.lifecycle_status == LIFECYCLE_ACTIVE,
        )
    )
    if org is None:
        return []
    now = datetime.now(UTC)
    candidates = (
        await db.execute(
            select(PublicChatSession.id)
            .where(
                PublicChatSession.organization_id == org,
                PublicChatSession.status == "human_requested",
                PublicChatSession.released_at.is_(None),
                PublicChatSession.updated_at >= since,
                PublicChatSession.expires_at > now,
            )
            .order_by(PublicChatSession.updated_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    staged: list[tuple[int, int]] = []
    for session_id in candidates:
        job_id = await stage_initial_handoff(db, organization_id=int(org), session_id=session_id)
        if job_id is not None:
            staged.append((int(job_id), int(org)))
    await db.commit()
    return staged


async def _update_outcome(db: AsyncSession, job: IntegrationJob, **fields: Any) -> None:
    """Bounded payload update fenced to the current integration-job claim."""
    from app.services.integration_job_service import JobDeferredError

    clean = dict(job.payload or {})
    clean.update(fields)
    updated = (
        await db.execute(
            update(IntegrationJob)
            .where(
                IntegrationJob.id == job.id,
                IntegrationJob.status == "processing",
                IntegrationJob.locked_at == job.locked_at,
            )
            .values(payload=clean)
            .returning(IntegrationJob.id)
        )
    ).scalar_one_or_none()
    if updated is None:
        await db.rollback()
        raise JobDeferredError("notification claim was lost")
    await db.commit()
    job.payload = clean


def _config_state() -> str:
    """Worker with missing GitHub variables must DEFER, not suppress.

    Explicit OFF is an owner kill switch; absent, empty, unparseable or
    otherwise unusable rollout configuration means the runner cannot make the
    decision and the alert must stay durable. The flag is interpreted with the
    same parser ``Settings`` uses, so the two can never disagree.
    """
    from app.core.config import parse_handoff_flag

    flag = parse_handoff_flag(os.environ.get("HANDOFF_NOTIFICATIONS_ENABLED"))
    if flag is False:
        return "off"
    if flag is True and settings.handoff_notifications_enabled:
        return "on"
    return "missing"


async def _suppressed(
    db: AsyncSession, job: IntegrationJob, reason: str, **extra: Any
) -> None:
    allowed = {"disabled", "inactive", "stale", "unconfigured", "rate_limited", "nothing_waiting"}
    if reason not in allowed:
        raise ValueError("unknown suppression reason")
    await _update_outcome(db, job, outcome=f"suppressed:{reason}", **extra)
    record_handoff_notification(outcome=f"suppressed_{reason}")
    log.info("handoff_notification_suppressed", job_id=job.id, reason=reason)


def _destination(channel: str) -> str:
    if channel == "email":
        return settings.handoff_notification_email_url
    if channel == "telegram":
        return settings.handoff_notification_telegram_url
    raise ValueError("unsupported channel")


async def _send_channels(db: AsyncSession, job: IntegrationJob, *, count: int | None = None) -> None:
    """Send each of the two channels with acceptance persisted individually."""
    from app.services.apprise_sender import (
        NotificationDestinationError,
        NotificationTransportError,
        restricted_url,
        send_staff_handoff,
    )
    from app.services.delivery_lease import handoff_notification_delivery_lease
    from app.services.integration_job_service import JobDeferredError, PermanentJobError

    accepted = list((job.payload or {}).get("accepted_channels") or [])
    if not set(accepted) <= set(CHANNELS):
        raise PermanentJobError("invalid recorded notification channels")
    # All channels must be configured in each worker; otherwise defer before
    # contacting either one, to avoid a partial send on environment drift.
    channels = required_channels()
    for channel in channels:
        destination = _destination(channel)
        if not destination:
            record_handoff_notification(outcome="deferred_config_missing")
            raise JobDeferredError("notification worker missing configuration")
        try:
            restricted_url(channel, destination)
        except NotificationDestinationError:
            # Present but invalid is still a configuration problem on THIS
            # runner: keep the alert durable (no attempt consumed, nothing
            # sent) so it goes out once the secret is corrected, instead of
            # being silently consumed as "suppressed".
            record_handoff_notification(outcome="deferred_config_missing")
            raise JobDeferredError("notification destination configuration invalid") from None

    queue_url = settings.frontend_base_url.rstrip("/") + "/public-chat"
    if not queue_url.startswith("https://"):
        raise PermanentJobError("staff handoff URL must be HTTPS")
    title = "CXOps AI — Human review requested"
    body = (
        "CXOps AI: " + (
            f"{count} additional handoff requests were recorded. "
            if count is not None else
            "A customer conversation is waiting for staff review. "
        ) + "Open the authenticated Handoff Queue: " + queue_url
    )

    # Advisory lease prevents simultaneous external attempts after a stale
    # claim is re-acquired. Socket timeouts bound each blocking plugin call.
    await release_idle_connection(db)
    async with handoff_notification_delivery_lease(job.id) as acquired:
        if not acquired:
            record_handoff_notification(outcome="deferred_busy")
            raise JobDeferredError("notification delivery busy")
        for channel in channels:
            if channel in accepted:
                continue
            try:
                await send_staff_handoff(
                    channel=channel,
                    configured=_destination(channel),
                    title=title,
                    body=body,
                )
            except NotificationDestinationError:
                record_handoff_notification(outcome="deferred_config_missing")
                raise JobDeferredError("notification destination configuration invalid") from None
            except NotificationTransportError:
                record_handoff_notification(outcome="transport_error")
                raise RuntimeError("notification delivery failed") from None
            accepted.append(channel)
            record_handoff_notification(outcome=f"channel_accepted_{channel}")
            await _update_outcome(db, job, accepted_channels=accepted)
            log.info("handoff_notification_channel_accepted", job_id=job.id, channel=channel)

    await _update_outcome(db, job, outcome="provider_accepted")
    record_handoff_notification(outcome="provider_accepted")


async def _process_initial(db: AsyncSession, job: IntegrationJob) -> None:
    from app.services.integration_job_service import PermanentJobError

    if job.organization_id is None:
        raise PermanentJobError("notification has no tenant binding")
    payload = job.payload
    session_id = payload.get("session_id")
    if (
        set(payload) - {"session_id", "event", "quota_granted", "accepted_channels", "outcome", "digest_hour"}
        or isinstance(session_id, bool)
        or not isinstance(session_id, int)
        or session_id <= 0
        or job.dedupe_key != first_handoff_key(job.organization_id, session_id)
    ):
        raise PermanentJobError("invalid handoff notification job")
    organization = await db.scalar(
        select(Organization.id).where(
            Organization.id == job.organization_id,
            Organization.external_id == settings.handoff_notification_pilot_tenant,
            Organization.lifecycle_status == LIFECYCLE_ACTIVE,
        )
    )
    if organization is None:
        await _suppressed(db, job, "inactive")
        return
    waiting = await db.scalar(
        select(PublicChatSession.id)
        .join(PublicChatConfiguration, PublicChatConfiguration.id == PublicChatSession.configuration_id)
        .where(
            PublicChatSession.id == session_id,
            PublicChatSession.organization_id == job.organization_id,
            PublicChatConfiguration.organization_id == job.organization_id,
            PublicChatConfiguration.enabled.is_(True),
            PublicChatSession.status == "human_requested",
            PublicChatSession.expires_at > datetime.now(UTC),
        )
    )
    if waiting is None:
        await _suppressed(db, job, "stale")
        return
    if "quota_granted" not in payload:
        allowed = await public_chat_rate_limiter_db.allow(
            db, scope="handoff_notify", key=f"org:{job.organization_id}",
            limit=settings.handoff_notification_max_per_hour,
            window_seconds=3600.0,
        )
        if not allowed:
            hour_start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            digest = (
                pg_insert(IntegrationJob)
                .values(
                    dedupe_key=digest_key(job.organization_id, hour_start),
                    organization_id=job.organization_id,
                    job_type=NOTIFY_JOB_TYPE,
                    payload={"event": "digest", "hour_start": hour_start.isoformat()},
                    status="pending", attempts=0, max_attempts=3,
                    available_at=hour_start + timedelta(hours=1, seconds=5),
                )
                .on_conflict_do_nothing(index_elements=[IntegrationJob.dedupe_key])
            )
            await db.execute(digest)
            # The digest counts by the hour a request was SUPPRESSED in (the
            # cap's own window), not by when the job row was created.
            await _suppressed(
                db, job, "rate_limited", digest_hour=hour_start.isoformat()
            )
            return
        await _update_outcome(db, job, quota_granted=True)
    await _send_channels(db, job)


async def _process_digest(db: AsyncSession, job: IntegrationJob) -> None:
    from app.services.integration_job_service import PermanentJobError

    if job.organization_id is None:
        raise PermanentJobError("digest has no tenant binding")
    payload = job.payload or {}
    raw = payload.get("hour_start")
    if not isinstance(raw, str):
        raise PermanentJobError("invalid digest hour")
    try:
        hour = datetime.fromisoformat(raw).astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise PermanentJobError("invalid digest hour") from None
    if (
        set(payload) - {"event", "hour_start", "accepted_channels", "outcome"}
        or hour.minute or hour.second or hour.microsecond
        or job.dedupe_key != digest_key(job.organization_id, hour)
    ):
        raise PermanentJobError("invalid digest job")
    if not await _correct_tenant(db, job.organization_id):
        await _suppressed(db, job, "inactive")
        return
    now = datetime.now(UTC)
    if now < hour + timedelta(hours=1):
        from app.services.integration_job_service import JobDeferredError
        raise JobDeferredError("notification digest not due")
    count = await db.scalar(
        select(func.count(IntegrationJob.id))
        .where(
            IntegrationJob.organization_id == job.organization_id,
            IntegrationJob.job_type == NOTIFY_JOB_TYPE,
            IntegrationJob.payload["outcome"].astext == "suppressed:rate_limited",
            IntegrationJob.payload["digest_hour"].astext == hour.isoformat(),
        )
    )
    if not count:
        await _suppressed(db, job, "nothing_waiting")
        return
    await _send_channels(db, job, count=int(count))


async def process_handoff_job(db: AsyncSession, job: IntegrationJob) -> None:
    """Worker entry: fail closed, never trust job payload identity alone."""
    from app.services.integration_job_service import JobDeferredError, PermanentJobError

    if job.organization_id is None or not isinstance(job.payload, dict):
        raise PermanentJobError("notification has no tenant binding")
    state = _config_state()
    if state == "missing":
        record_handoff_notification(outcome="deferred_config_missing")
        raise JobDeferredError("notification worker configuration missing")
    if state == "off":
        await _suppressed(db, job, "disabled")
        return
    event = job.payload.get("event")
    if event == "initial":
        await _process_initial(db, job)
    elif event == "digest":
        await _process_digest(db, job)
    else:
        raise PermanentJobError("invalid notification event")
