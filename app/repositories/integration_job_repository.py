from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_job import IntegrationJob


class IntegrationJobRepository:

    @staticmethod
    async def get_by_dedupe_key(
        db: AsyncSession,
        dedupe_key: str,
    ) -> IntegrationJob | None:

        result = await db.execute(
            select(IntegrationJob).where(
                IntegrationJob.dedupe_key == dedupe_key
            )
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
    async def claim_next(
        db: AsyncSession,
    ) -> IntegrationJob | None:
    
        now = datetime.now(timezone.utc)
    
        stale_before = now - timedelta(
            seconds=60
        )
    
        # Recover jobs left in "processing"
        # after a worker crash/restart.
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
                last_error="Recovered stale processing job",
            )
        )
    
        await db.commit()
    
        result = await db.execute(
            select(IntegrationJob)
            .where(
                IntegrationJob.status.in_(
                    ["pending", "retry"]
                ),
                IntegrationJob.available_at <= now,
            )
            .order_by(
                IntegrationJob.id.asc()
            )
            .with_for_update(
                skip_locked=True
            )
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
        job: IntegrationJob,
    ) -> None:

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.locked_at = None
        job.last_error = None

        await db.commit()

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        job: IntegrationJob,
        error: str,
    ) -> None:

        if job.attempts >= job.max_attempts:
            job.status = "failed"

        else:
            job.status = "retry"

            delay_seconds = min(
                2 ** job.attempts,
                300,
            )

            job.available_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=delay_seconds)
            )

        job.locked_at = None
        job.last_error = error[:4000]

        await db.commit()