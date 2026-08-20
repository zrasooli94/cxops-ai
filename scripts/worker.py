import asyncio

from prometheus_client import (
    start_http_server,
)

from app.core.database import (
    AsyncSessionLocal,
)
from app.core.metrics import (
    record_integration_job_completed,
    record_integration_job_failure,
    record_integration_job_retry,
)
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.services.integration_job_service import (
    IntegrationJobService,
)

WORKER_METRICS_PORT = 9101


async def run_worker() -> None:

    print("CXOps worker started")

    print(f"CXOps worker metrics: http://127.0.0.1:{WORKER_METRICS_PORT}/")

    while True:
        async with AsyncSessionLocal() as db:
            job = await IntegrationJobRepository.claim_next(db)

            if job is None:
                await asyncio.sleep(1)
                continue

            job_id = job.id
            job_type = job.job_type
            attempt = job.attempts

            print(f"[worker] processing job={job_id} type={job_type} attempt={attempt}")

            try:
                await IntegrationJobService.execute(
                    db=db,
                    job=job,
                )

                await IntegrationJobRepository.mark_completed(
                    db=db,
                    job_id=job_id,
                )

                record_integration_job_completed(
                    job_type=job_type,
                )

                print(f"[worker] completed job={job_id}")

            except Exception as exc:  # noqa: BLE001
                await db.rollback()

                updated_job = await IntegrationJobRepository.mark_failed(
                    db=db,
                    job_id=job_id,
                    error_message=str(exc),
                )

                if updated_job is not None:
                    if updated_job.status == "retry":
                        record_integration_job_retry(
                            job_type=job_type,
                        )

                    elif updated_job.status == "failed":
                        record_integration_job_failure(
                            job_type=job_type,
                        )

                print(f"[worker] failed job={job_id}: {exc}")


if __name__ == "__main__":
    start_http_server(WORKER_METRICS_PORT)

    asyncio.run(run_worker())
