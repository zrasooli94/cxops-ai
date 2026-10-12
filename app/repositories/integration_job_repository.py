from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_job import IntegrationJob

# A ``processing`` job is only treated as abandoned (worker crash) after this
# long. It MUST exceed the longest legitimate execution: the one-shot worker's
# 240-second budget lets an in-flight job finish past the deadline, and the
# platform backstop is a 6-minute job timeout. Recovering a job that is still
# running would execute it twice concurrently, so the window errs long: a
# crashed job waits at most ten minutes for recovery.
STALE_PROCESSING_SECONDS = 600

STALE_EXHAUSTED_ERROR = "Stale processing job exceeded max attempts"


def _claim_is_current(job: IntegrationJob, claimed_at: datetime | None) -> bool:
    """Fencing: only the worker holding the CURRENT claim may finish a job.

    ``claimed_at`` is the ``locked_at`` value the caller received when it
    claimed the job. If the job was recovered and re-claimed meanwhile, its
    ``locked_at`` changed and the late worker's report is rejected instead of
    overwriting the newer claim. ``None`` keeps the legacy unfenced behavior
    for callers that do not hold a claim token.
    """
    if claimed_at is None:
        return True
    return job.status == "processing" and job.locked_at == claimed_at


class IntegrationJobRepository:
    @staticmethod
    def add(
        db: AsyncSession,
        job: IntegrationJob,
    ) -> None:
        """Stage a job inside the caller's transaction (no commit)."""
        db.add(job)

    @staticmethod
    async def flush(
        db: AsyncSession,
    ) -> None:
        """Flush pending job so its generated id is available."""
        await db.flush()

    @staticmethod
    async def get_by_id_unscoped(
        db: AsyncSession,
        job_id: int,
    ) -> IntegrationJob | None:
        """INTERNAL-ONLY unscoped job lookup.

        Binds no tenant and MUST NOT be used by any human / JWT-authenticated
        / public route. Exists solely for the durable job worker: the worker
        claims a job and ``IntegrationJobService.execute`` re-validates the
        job's persisted ``organization_id`` chain before any side effect.
        """

        result = await db.execute(
            select(IntegrationJob).where(IntegrationJob.id == job_id)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_dedupe_key(
        db: AsyncSession,
        dedupe_key: str,
    ) -> IntegrationJob | None:

        result = await db.execute(
            select(IntegrationJob).where(IntegrationJob.dedupe_key == dedupe_key)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        job: IntegrationJob,
    ) -> IntegrationJob:

        db.add(job)

        await db.commit()
        await db.refresh(job)

        return job

    @staticmethod
    async def requeue_failed_for_tenant(
        db: AsyncSession,
        *,
        dedupe_key: str,
        organization_id: int,
    ) -> IntegrationJob | None:
        """Reset a failed job for manual retry, scoped to the tenant.

        Only a permanently failed job may be requeued. The same dedupe key and
        organization are preserved so the retry remains the same durable unit
        of work.
        """
        result = await db.execute(
            select(IntegrationJob).where(
                IntegrationJob.dedupe_key == dedupe_key,
                IntegrationJob.organization_id == organization_id,
                IntegrationJob.status == "failed",
            )
        )

        job = result.scalar_one_or_none()
        if job is None:
            return None

        job.status = "retry"
        job.attempts = 0
        job.locked_at = None
        job.last_error = None
        job.available_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(job)

        return job

    @staticmethod
    async def claim_next_unscoped(
        db: AsyncSession,
    ) -> IntegrationJob | None:
        """INTERNAL-ONLY unscoped worker claim.

        The durable job worker must be able to pick the next pending job across
        every tenant, so this claim carries no organization bound — this is the
        one legitimate global read in the subsystem. ``IntegrationJobService``
        then executes each claimed job strictly within its persisted
        ``organization_id`` (agent-execution and Zendesk jobs both verify the
        org chain before any side effect), so a cross-tenant claim cannot
        cause a cross-tenant write. Has no public/tenant route caller.
        """

        now = datetime.now(timezone.utc)

        stale_before = now - timedelta(seconds=STALE_PROCESSING_SECONDS)

        # Recover jobs left in processing after a worker crash/restart. A job
        # that already used every attempt is failed instead of recovered: a job
        # that crashes the worker each time (poison pill) would otherwise be
        # recovered forever, with ``attempts`` climbing past ``max_attempts``.
        await db.execute(
            update(IntegrationJob)
            .where(
                IntegrationJob.status == "processing",
                IntegrationJob.locked_at.is_not(None),
                IntegrationJob.locked_at < stale_before,
                IntegrationJob.attempts >= IntegrationJob.max_attempts,
            )
            .values(
                status="failed",
                locked_at=None,
                last_error=STALE_EXHAUSTED_ERROR,
            )
        )
        await db.execute(
            update(IntegrationJob)
            .where(
                IntegrationJob.status == "processing",
                IntegrationJob.locked_at.is_not(None),
                IntegrationJob.locked_at < stale_before,
            )
            .values(
                status="retry",
                locked_at=None,
                available_at=now,
                last_error=("Recovered stale processing job"),
            )
        )

        await db.commit()

        result = await db.execute(
            select(IntegrationJob)
            .where(
                IntegrationJob.status.in_(
                    [
                        "pending",
                        "retry",
                    ]
                ),
                IntegrationJob.available_at <= now,
            )
            .order_by(IntegrationJob.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )

        job = result.scalar_one_or_none()

        if job is None:
            return None

        job.status = "processing"
        job.attempts += 1
        job.locked_at = now

        await db.commit()
        await db.refresh(job)

        return job

    @staticmethod
    async def mark_completed(
        db: AsyncSession,
        *,
        job_id: int,
        claimed_at: datetime | None = None,
    ) -> IntegrationJob | None:
        """Complete a job. Returns ``None`` if it is gone or the claim was lost."""

        job = await IntegrationJobRepository.get_by_id_unscoped(
            db,
            job_id,
        )

        if job is None or not _claim_is_current(job, claimed_at):
            return None

        job.status = "completed"

        job.completed_at = datetime.now(timezone.utc)

        job.locked_at = None
        job.last_error = None

        await db.commit()
        await db.refresh(job)

        return job

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        *,
        job_id: int,
        error_message: str,
        claimed_at: datetime | None = None,
        terminal: bool = False,
    ) -> IntegrationJob | None:
        """Record a failed attempt: ``retry`` with backoff, or terminal ``failed``.

        ``terminal=True`` (a permanent, non-retryable error) fails the job now
        instead of spending its remaining attempts. Returns ``None`` if the job
        is gone or the caller's claim was lost (see ``_claim_is_current``).
        """

        now = datetime.now(timezone.utc)

        job = await IntegrationJobRepository.get_by_id_unscoped(
            db,
            job_id,
        )

        if job is None or not _claim_is_current(job, claimed_at):
            return None

        if terminal or job.attempts >= job.max_attempts:
            job.status = "failed"

        else:
            job.status = "retry"

            delay_seconds = min(
                2**job.attempts,
                300,
            )

            job.available_at = now + timedelta(seconds=delay_seconds)

        job.locked_at = None

        job.last_error = error_message[:4000]

        await db.commit()
        await db.refresh(job)

        return job
