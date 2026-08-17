import asyncio

from app.core.database import AsyncSessionLocal
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.services.integration_job_service import (
    IntegrationJobService,
)


async def run_worker():
    print("CXOps worker started")

    while True:
        async with AsyncSessionLocal() as db:

            job = await IntegrationJobRepository.claim_next(
                db
            )

            if job is None:
                await asyncio.sleep(1)
                continue

            print(
                f"[worker] processing job={job.id} "
                f"type={job.job_type} "
                f"attempt={job.attempts}"
            )

            try:
                await IntegrationJobService.execute(
                    db=db,
                    job=job,
                )

                await IntegrationJobRepository.mark_completed(
                    db=db,
                    job=job,
                )

                print(
                    f"[worker] completed job={job.id}"
                )

            except Exception as exc:
                await db.rollback()

                await IntegrationJobRepository.mark_failed(
                    db=db,
                    job=job,
                    error=str(exc),
                )

                print(
                    f"[worker] failed job={job.id}: "
                    f"{exc}"
                )


if __name__ == "__main__":
    asyncio.run(run_worker())