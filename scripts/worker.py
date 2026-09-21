import asyncio

from prometheus_client import (
    start_http_server,
)

from app.core.database import (
    AsyncSessionLocal,
)
from app.core.logging import (
    bind_context,
    configure_logging,
    get_logger,
    unbind_context,
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

configure_logging()

log = get_logger("worker")


async def run_worker() -> None:

    log.info("worker_started", metrics_port=WORKER_METRICS_PORT)

    while True:
        async with AsyncSessionLocal() as db:
            job = await IntegrationJobRepository.claim_next_unscoped(db)

            if job is None:
                await asyncio.sleep(1)
                continue

            job_id = job.id
            job_type = job.job_type
            attempt = job.attempts

            # Extract and bind request correlation ID from job payload
            request_id = job.payload.get("request_id") if job.payload else None
            bind_context(job_id=str(job_id), action=job_type, request_id=request_id)

            log.info(
                "processing_job", job_id=job_id, job_type=job_type, attempt=attempt
            )

            try:
                await IntegrationJobService.execute(
                    db=db,
                    job=job,
                )

                # execute() may have already marked the job terminal for
                # non-retryable failures (e.g. conversation.reply). Only mark
                # completed if the job is still in processing.
                fresh_job = await IntegrationJobRepository.get_by_id_unscoped(
                    db=db,
                    job_id=job_id,
                )
                if fresh_job is not None and fresh_job.status == "processing":
                    await IntegrationJobRepository.mark_completed(
                        db=db,
                        job_id=job_id,
                    )

                    record_integration_job_completed(
                        job_type=job_type,
                    )

                    log.info("job_completed", job_id=job_id, job_type=job_type)

                elif fresh_job is not None and fresh_job.status == "failed":
                    record_integration_job_failure(
                        job_type=job_type,
                    )
                    bind_context(
                        outcome="failed", error_category="non_retryable"
                    )
                    log.error(
                        "job_failed",
                        job_id=job_id,
                        job_type=job_type,
                        error=fresh_job.last_error,
                    )

            except Exception as exc:  # noqa: BLE001
                await db.rollback()

                updated_job = await IntegrationJobRepository.mark_failed(
                    db=db,
                    job_id=job_id,
                    error_message=str(exc),
                )

                await IntegrationJobService.handle_failure(
                    db=db,
                    job=updated_job,
                    error_message=str(exc),
                )

                if updated_job is not None:
                    if updated_job.status == "retry":
                        record_integration_job_retry(
                            job_type=job_type,
                        )
                        bind_context(outcome="retry")
                        log.warning(
                            "job_scheduled_retry",
                            job_id=job_id,
                            job_type=job_type,
                            error=str(exc),
                        )

                    elif updated_job.status == "failed":
                        record_integration_job_failure(
                            job_type=job_type,
                        )
                        bind_context(
                            outcome="failed", error_category=type(exc).__name__
                        )
                        log.error(
                            "job_failed",
                            job_id=job_id,
                            job_type=job_type,
                            error=str(exc),
                        )

                else:
                    bind_context(outcome="failed", error_category=type(exc).__name__)
                    log.error(
                        "job_failed_no_update",
                        job_id=job_id,
                        job_type=job_type,
                        error=str(exc),
                    )

            finally:
                unbind_context()


if __name__ == "__main__":
    start_http_server(WORKER_METRICS_PORT)

    asyncio.run(run_worker())
