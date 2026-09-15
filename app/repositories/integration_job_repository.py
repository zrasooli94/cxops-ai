from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_job import IntegrationJob


class IntegrationJobRepository:
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

        stale_before = now - timedelta(seconds=60)

        # Recover jobs left in processing
        # after worker crash/restart.
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
    ) -> IntegrationJob | None:

        job = await IntegrationJobRepository.get_by_id_unscoped(
            db,
            job_id,
        )

        if job is None:
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
    ) -> IntegrationJob | None:

        now = datetime.now(timezone.utc)

        job = await IntegrationJobRepository.get_by_id_unscoped(
            db,
            job_id,
        )

        if job is None:
            return None

        if job.attempts >= job.max_attempts:
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
