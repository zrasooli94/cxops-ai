import asyncio

from app.core.database import AsyncSessionLocal
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.services.integration_job_service import (
    IntegrationJobService,
)


async def run_worker() -> None:
    print("CXOps worker started")

    while True:
        async with AsyncSessionLocal() as db:

            job = await (
                IntegrationJobRepository.claim_next(
                    db
                )
            )

            if job is None:
                await asyncio.sleep(1)
                continue

            # Store primitive values before executing the job.
            #
            # Other services may commit or rollback the SQLAlchemy
            # session, which can expire/detach the original ORM object.
            job_id = job.id
            job_type = job.job_type
            attempt = job.attempts

            print(
                f"[worker] processing "
                f"job={job_id} "
                f"type={job_type} "
                f"attempt={attempt}"
            )

            try:
                await IntegrationJobService.execute(
                    db=db,
                    job=job,
                )

                await (
                    IntegrationJobRepository
                    .mark_completed(
                        db=db,
                        job_id=job_id,
                    )
                )

                print(
                    f"[worker] completed "
                    f"job={job_id}"
                )

            except Exception as exc:
                await db.rollback()

                await (
                    IntegrationJobRepository
                    .mark_failed(
                        db=db,
                        job_id=job_id,
                        error_message=str(exc),
                    )
                )

                print(
                    f"[worker] failed "
                    f"job={job_id}: {exc}"
                )


if __name__ == "__main__":
    asyncio.run(run_worker())