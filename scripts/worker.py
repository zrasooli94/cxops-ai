import argparse
import asyncio
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from prometheus_client import (
    start_http_server,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.database import (
    AsyncSessionLocal,
    engine,
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
from app.models.integration_job import IntegrationJob
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.services.integration_job_service import (
    IntegrationJobService,
)
from app.services.sla_escalation_scanner_service import (
    SLAEscalationScannerService,
)

WORKER_METRICS_PORT = 9101

# Stable advisory-lock key for the global SLA escalation scanner.
# Only one worker replica should scan at a time.
SLA_SCANNER_LOCK_KEY = 0x1A4C_1A4C_1A4C_1A4C
SLA_SCAN_INTERVAL_SECONDS = 60

# Bounds for the one-shot entry point (`python -m scripts.worker --once`).
#
# The one-shot run is a scheduled delivery mechanism: a fresh process claims
# what is ready, drains it, and exits. Both numbers exist so a single run can
# never outlive the interval that triggered it -- a run that outlasted its own
# schedule would overlap the next one, and overlap is only safe because job
# claiming is atomic, not because it is cheap to reason about.
#
# 25 jobs at 240 seconds is a pilot figure, deliberately conservative: the
# application-level deadline is the real bound, and the GitHub job timeout is a
# platform backstop behind it.
DEFAULT_ONCE_MAX_JOBS = 25
DEFAULT_ONCE_MAX_SECONDS = 240.0

configure_logging()

log = get_logger("worker")


def resolve_metrics_port() -> int | None:
    """Port for the Prometheus listener, or None to run without it.

    The listener is on by default because "is the worker alive" is the first
    question during an incident, and a worker that claims no jobs is
    indistinguishable from a dead one in the logs.

    A deployment whose platform forbids extra listeners sets
    ``WORKER_METRICS_PORT=0`` rather than being forced to accept a crash. Kept
    here, next to the binding, so the environment contract in
    scripts/start_replit_worker.sh cannot drift from the code that implements it.
    """
    raw = os.environ.get("WORKER_METRICS_PORT", "").strip()
    if raw.lower() in ("0", "off", "false", "none"):
        return None
    if not raw:
        return WORKER_METRICS_PORT
    try:
        port = int(raw)
    except ValueError:
        log.warning(
            "worker_metrics_port_invalid",
            reason="not an integer; using default",
        )
        return WORKER_METRICS_PORT
    if not 1 <= port <= 65535:
        log.warning("worker_metrics_port_invalid", reason="out of range; using default")
        return WORKER_METRICS_PORT
    return port


async def _try_acquire_scanner_lock(
    lock_conn: AsyncConnection,
) -> bool:
    result = await lock_conn.execute(
        text("SELECT pg_try_advisory_lock(:lock_key)"),
        {"lock_key": SLA_SCANNER_LOCK_KEY},
    )
    acquired = result.scalar()
    return bool(acquired)


async def _release_scanner_lock(
    lock_conn: AsyncConnection,
) -> None:
    result = await lock_conn.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": SLA_SCANNER_LOCK_KEY},
    )
    released = result.scalar()
    if not released:
        log.error(
            "scanner_lock_release_failed",
            lock_key=SLA_SCANNER_LOCK_KEY,
        )


async def run_sla_scan_cycle() -> None:
    """Run one bounded SLA escalation scan if the advisory lock is available."""
    lock_conn: AsyncConnection | None = None
    try:
        lock_conn = await engine.connect()
        acquired = await _try_acquire_scanner_lock(lock_conn)
        if not acquired:
            log.debug("scanner_lock_unavailable", lock_key=SLA_SCANNER_LOCK_KEY)
            return

        log.info("sla_scan_started")
        async with AsyncSessionLocal() as db:
            evaluated = await SLAEscalationScannerService.scan_once(
                db,
                now=datetime.now(UTC),
            )
        log.info("sla_scan_completed", evaluated=evaluated)

    except Exception:
        log.exception("sla_scan_failed")
    finally:
        if lock_conn is not None:
            try:
                await _release_scanner_lock(lock_conn)
            finally:
                await lock_conn.close()


async def process_claimed_job(db: AsyncSession, job: IntegrationJob) -> None:
    """Run one already-claimed job to a terminal-or-retry decision.

    This is the whole job-processing body, shared verbatim by the infinite
    worker and the one-shot worker. It exists as one function because the two
    modes differ only in *when they stop*, and a copy of this block would be a
    place where the two modes' completion, failure, and retry semantics could
    quietly diverge -- the exact defect class a durable job queue punishes
    hardest, since the divergence would only be visible in production rows.

    ``db`` must be the same session the job was claimed on: ``execute`` writes
    through it, and the terminal re-read below has to observe what ``execute``
    just wrote.
    """
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


async def run_worker() -> None:

    _metrics_port = resolve_metrics_port()
    log.info(
        "worker_started",
        metrics_port=_metrics_port if _metrics_port is not None else "disabled",
    )

    last_scan_time = 0.0

    while True:
        now = time.monotonic()
        if now - last_scan_time >= SLA_SCAN_INTERVAL_SECONDS:
            await run_sla_scan_cycle()
            last_scan_time = now

        async with AsyncSessionLocal() as db:
            job = await IntegrationJobRepository.claim_next_unscoped(db)

            if job is None:
                await asyncio.sleep(1)
                continue

            await process_claimed_job(db, job)


async def run_worker_once(
    max_jobs: int = DEFAULT_ONCE_MAX_JOBS,
    max_seconds: float = DEFAULT_ONCE_MAX_SECONDS,
) -> dict[str, float | int | str]:
    """Drain the queue once and exit. Never waits, never serves metrics.

    This is the scheduled pilot worker: a process that starts, does the work
    that is already durable, and terminates. Every difference from
    :func:`run_worker` below is a consequence of that lifecycle.

    Ordering of the three stop conditions is deliberate:

    1. the bounds are checked *before* claiming, so ``max_jobs``/``max_seconds``
       can never leave a job claimed and then abandoned by a process that is on
       its way out;
    2. an empty queue stops immediately rather than sleeping, because the next
       scheduled run is the retry mechanism -- sleeping here would only hold a
       runner slot between two ticks;
    3. the deadline is checked between jobs, never *during* one. A job already
       claimed is allowed to finish even if ``max_seconds`` passes mid-execution:
       cancelling it would strand a ``processing`` row for the stale-recovery
       window, which is strictly worse than a run that overruns its bound. The
       GitHub job timeout is the backstop for that case, not a cancellation.

    Job claiming remains the atomic, row-locked ``claim_next_unscoped``, so an
    overlap with another worker (including the always-on Render worker) is a
    correctness non-event rather than a race.

    Returns only safe metadata -- counts, a stop reason, and elapsed time. No
    payload, no URL, no credential ever reaches the summary line.
    """
    started = time.monotonic()
    deadline = started + max_seconds

    log.info(
        "worker_once_started",
        max_jobs=max_jobs,
        max_seconds=max_seconds,
    )

    # Exactly one SLA scan per run. The advisory lock inside it is what makes
    # running this every five minutes safe alongside an always-on worker: if a
    # continuous worker already holds the lock, this run skips silently.
    await run_sla_scan_cycle()

    jobs_processed = 0
    exit_reason = "queue_empty"

    while True:
        if jobs_processed >= max_jobs:
            exit_reason = "max_jobs"
            break

        if time.monotonic() >= deadline:
            exit_reason = "max_seconds"
            break

        async with AsyncSessionLocal() as db:
            job = await IntegrationJobRepository.claim_next_unscoped(db)

            if job is None:
                break

            await process_claimed_job(db, job)

        jobs_processed += 1

    elapsed_seconds = time.monotonic() - started
    log.info(
        "worker_once_completed",
        jobs_processed=jobs_processed,
        exit_reason=exit_reason,
        elapsed_seconds=round(elapsed_seconds, 3),
    )

    return {
        "jobs_processed": jobs_processed,
        "exit_reason": exit_reason,
        "elapsed_seconds": elapsed_seconds,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.worker",
        description=(
            "CXOps durable integration-job worker. "
            "Without --once it runs until stopped; with --once it drains the "
            "queue and exits."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "run a single bounded pass: one SLA scan, then jobs until the "
            "queue is empty or a bound is reached, then exit. Never sleeps "
            "waiting for work and never starts the metrics listener."
        ),
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=DEFAULT_ONCE_MAX_JOBS,
        metavar="N",
        help=(
            "maximum jobs to claim in --once mode "
            f"(default: {DEFAULT_ONCE_MAX_JOBS}, minimum: 1)"
        ),
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_ONCE_MAX_SECONDS,
        metavar="S",
        help=(
            "wall-clock deadline for --once mode, checked between jobs; a job "
            f"already in flight is allowed to finish "
            f"(default: {DEFAULT_ONCE_MAX_SECONDS}, must be > 0)"
        ),
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate the CLI before anything is connected or started.

    Validation lives here rather than inside ``run_worker_once`` so that an
    operator's typo fails with argparse's usage message and exit code 2 -- no
    database connection, no worker boot, no ambiguous success. A ``--max-jobs``
    of 0 would exit immediately reporting success while doing nothing, which is
    the most dangerous shape of invalid input: it looks like a quiet queue.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.max_jobs < 1:
        parser.error(f"--max-jobs must be >= 1 (got {args.max_jobs})")
    if args.max_seconds <= 0:
        parser.error(f"--max-seconds must be > 0 (got {args.max_seconds})")

    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.once:
        # No metrics listener, deliberately. One-shot runs live for seconds in
        # a platform that tears the runner down; a Prometheus socket would be
        # unscrapeable for its whole lifetime and its port would fight the
        # next run. The listener belongs to the process that stays up.
        asyncio.run(
            run_worker_once(
                max_jobs=args.max_jobs,
                max_seconds=args.max_seconds,
            )
        )
        return 0

    _metrics_port = resolve_metrics_port()
    if _metrics_port is not None:
        start_http_server(_metrics_port)

    asyncio.run(run_worker())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
