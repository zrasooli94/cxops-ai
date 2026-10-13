from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, update
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


async def _fenced_update(
    db: AsyncSession,
    *,
    job_id: int,
    claimed_at: datetime | None,
    values: dict[str, Any],
) -> bool:
    """Apply ``values`` to the job ONLY if the caller still holds its claim.

    One ``UPDATE ... WHERE id = :id AND status = 'processing' AND locked_at =
    :claimed_at RETURNING id`` statement: the claim check and the write are a
    single atomic database operation, so no other worker can re-claim the row
    between them (a Python check on a previously read row cannot guarantee
    that). The row lock taken by the UPDATE is held until the caller's commit,
    so a concurrent stale recovery waits and then sees the new state.

    ``claimed_at=None`` keeps the legacy unfenced write for callers that hold
    no claim token (never the worker). Does not commit.
    """
    stmt = update(IntegrationJob).where(IntegrationJob.id == job_id)
    if claimed_at is not None:
        stmt = stmt.where(
            IntegrationJob.status == "processing",
            IntegrationJob.locked_at == claimed_at,
        )
    stmt = (
        stmt.values(**values)
        .returning(IntegrationJob.id)
        .execution_options(synchronize_session=False)
    )
    return (await db.execute(stmt)).first() is not None


async def _reload(db: AsyncSession, job_id: int) -> IntegrationJob | None:
    result = await db.execute(
        select(IntegrationJob)
        .where(IntegrationJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


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
    async def list_open_by_dedupe_prefix(
        db: AsyncSession,
        *,
        organization_id: int,
        job_type: str,
        dedupe_prefix: str,
        limit: int,
    ) -> list[IntegrationJob]:
        """Tenant-scoped, not-yet-finished jobs whose dedupe key starts with a
        prefix (e.g. one chat session's turns), oldest first."""
        escaped = (
            dedupe_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        result = await db.execute(
            select(IntegrationJob)
            .where(
                IntegrationJob.organization_id == organization_id,
                IntegrationJob.job_type == job_type,
                IntegrationJob.status.in_(("pending", "retry", "processing")),
                IntegrationJob.dedupe_key.like(f"{escaped}%", escape="\\"),
            )
            .order_by(IntegrationJob.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_recoverable(
        db: AsyncSession,
        *,
        job_type: str,
        stale_after_seconds: int,
        limit: int,
    ) -> list[IntegrationJob]:
        """INTERNAL-ONLY: due or abandoned jobs of one type, oldest first.

        Used once at API start-up to resume chat turns interrupted by a
        restart; bounded by ``limit``.
        """
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(IntegrationJob)
            .where(
                IntegrationJob.job_type == job_type,
                IntegrationJob.attempts < IntegrationJob.max_attempts,
                or_(
                    and_(
                        IntegrationJob.status.in_(("pending", "retry")),
                        IntegrationJob.available_at <= now,
                    ),
                    and_(
                        IntegrationJob.status == "processing",
                        IntegrationJob.locked_at
                        < now - timedelta(seconds=stale_after_seconds),
                    ),
                ),
            )
            .order_by(IntegrationJob.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_open_by_type(
        db: AsyncSession,
        *,
        job_type: str,
        after_id: int,
        limit: int,
    ) -> list[IntegrationJob]:
        """INTERNAL-ONLY: every unfinished job of one type, by id, one page.

        Phase 1P.9R recovery input: pending/retry jobs (due or not) and
        processing jobs (stale or not); the caller schedules each for when it
        becomes claimable. Keyset-paged on ``id`` so a large backlog is
        walked completely without OFFSET scans.
        """
        result = await db.execute(
            select(IntegrationJob)
            .where(
                IntegrationJob.job_type == job_type,
                IntegrationJob.id > after_id,
                IntegrationJob.status.in_(("pending", "retry", "processing")),
                IntegrationJob.attempts < IntegrationJob.max_attempts,
            )
            .order_by(IntegrationJob.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def claim_by_id(
        db: AsyncSession,
        *,
        job_id: int,
        job_type: str,
        stale_after_seconds: int,
    ) -> IntegrationJob | None:
        """Claim ONE specific job atomically, or return ``None``.

        Used by the in-process public-chat turn dispatcher, which knows the
        exact job it just enqueued. A single conditional ``UPDATE ... RETURNING``
        claims the job only if it is claimable right now - ``pending``/``retry``
        and due, or ``processing`` with a claim older than
        ``stale_after_seconds`` (its process died) - and still has attempts
        left. Two dispatchers (or a dispatcher and the worker) racing for the
        same job: exactly one wins. Commits.
        """
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=stale_after_seconds)
        claimable = or_(
            and_(
                IntegrationJob.status.in_(("pending", "retry")),
                IntegrationJob.available_at <= now,
            ),
            and_(
                IntegrationJob.status == "processing",
                IntegrationJob.locked_at.is_not(None),
                IntegrationJob.locked_at < stale_before,
            ),
        )
        result = await db.execute(
            update(IntegrationJob)
            .where(
                IntegrationJob.id == job_id,
                IntegrationJob.job_type == job_type,
                IntegrationJob.attempts < IntegrationJob.max_attempts,
                claimable,
            )
            .values(
                status="processing",
                attempts=IntegrationJob.attempts + 1,
                locked_at=now,
            )
            .returning(IntegrationJob.id)
            .execution_options(synchronize_session=False)
        )
        claimed = result.first() is not None
        await db.commit()
        if not claimed:
            return None
        return await _reload(db, job_id)

    @staticmethod
    async def mark_completed(
        db: AsyncSession,
        *,
        job_id: int,
        claimed_at: datetime | None = None,
    ) -> IntegrationJob | None:
        """Complete a job. Returns ``None`` if it is gone or the claim was lost.

        On a lost claim the caller's whole transaction is rolled back: a worker
        that no longer owns the job must not commit any of its side effects.
        """

        job = await IntegrationJobRepository.get_by_id_unscoped(
            db,
            job_id,
        )

        if job is None or not _claim_is_current(job, claimed_at):
            await db.rollback()
            return None

        applied = await _fenced_update(
            db,
            job_id=job_id,
            claimed_at=claimed_at,
            values={
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "locked_at": None,
                "last_error": None,
            },
        )
        if not applied:
            await db.rollback()
            return None

        await db.commit()
        return await _reload(db, job_id)

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

        # ``attempts`` read above cannot change while the claim is held: any
        # re-claim changes ``locked_at``, which the fenced UPDATE re-checks.
        values: dict[str, Any] = {
            "locked_at": None,
            "last_error": error_message[:4000],
        }
        if terminal or job.attempts >= job.max_attempts:
            values["status"] = "failed"
        else:
            values["status"] = "retry"
            delay_seconds = min(2**job.attempts, 300)
            values["available_at"] = now + timedelta(seconds=delay_seconds)

        applied = await _fenced_update(
            db, job_id=job_id, claimed_at=claimed_at, values=values
        )
        if not applied:
            await db.rollback()
            return None

        await db.commit()
        return await _reload(db, job_id)

    @staticmethod
    async def defer_claimed(
        db: AsyncSession,
        *,
        job_id: int,
        claimed_at: datetime | None,
        delay_seconds: int,
        reason: str,
    ) -> IntegrationJob | None:
        """Requeue a job WITHOUT consuming an attempt (another worker is busy
        with the same unit of work). Fenced like every terminal write; commits.
        """
        applied = await _fenced_update(
            db,
            job_id=job_id,
            claimed_at=claimed_at,
            values={
                "status": "retry",
                "locked_at": None,
                "attempts": func.greatest(IntegrationJob.attempts - 1, 0),
                "available_at": datetime.now(timezone.utc)
                + timedelta(seconds=delay_seconds),
                "last_error": reason[:4000],
            },
        )
        if not applied:
            await db.rollback()
            return None
        await db.commit()
        return await _reload(db, job_id)

    @staticmethod
    async def fail_claimed(
        db: AsyncSession,
        *,
        job_id: int,
        claimed_at: datetime | None,
        error_message: str,
    ) -> bool:
        """Fenced terminal failure inside the caller's transaction (no commit).

        Used by job handlers that must update other rows atomically with the
        job's terminal state. ``False`` means the claim was lost: the caller
        must roll back and write nothing.
        """
        return await _fenced_update(
            db,
            job_id=job_id,
            claimed_at=claimed_at,
            values={
                "status": "failed",
                "locked_at": None,
                "last_error": error_message[:4000],
            },
        )
