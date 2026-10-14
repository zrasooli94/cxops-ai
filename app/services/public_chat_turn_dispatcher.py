"""In-process dispatcher for durable public-chat AI turns (Phase 1P.9).

Why in-process: the visitor should get an "accepted" answer immediately and the
reply a few seconds later, without paying for or depending on an always-on
worker (the scheduled worker runs far too rarely to carry interactive chat).
The API process that accepted the message runs its turn right away, as a
background task, through the SAME durable job row the worker understands:

* claim: one fenced ``UPDATE ... RETURNING`` on that exact job
  (``IntegrationJobRepository.claim_by_id``); a stale claim (process died
  mid-turn) is re-claimable after ``public_chat_turn_stale_seconds``;
* execute: ``IntegrationJobService.execute`` -> ``PublicChatService.process_turn``
  (tenant-bound, idempotent, per-session advisory lease);
* finish: fenced ``mark_completed`` / ``mark_failed`` / ``defer_claimed``.

Bounded concurrency: a global semaphore plus one per tenant, so one busy
business cannot take every slot from the others.

Recovery without idle polling: pending or abandoned turns are re-submitted (a)
when the waiting visitor polls for updates, (b) at API start-up - every open
turn, paged, retried until the database answers, each scheduled for when its
claim becomes available - and (c) by an hourly in-process sweep (Phase 1P.9R)
for turns nobody is waiting on. The scheduled worker remains a backstop. A turn that still fails
permanently is converted into a human handoff, so a visitor is never left
waiting on a reply that will not come.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from datetime import UTC, datetime

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.integration_job import IntegrationJob
from app.repositories.integration_job_repository import IntegrationJobRepository
from app.services.integration_job_service import (
    EXECUTE_CLAIM_LOST,
    IntegrationJobService,
    JobDeferredError,
    PermanentJobError,
)

log = get_logger("public_chat_turn_dispatcher")

PUBLIC_CHAT_TURN_JOB_TYPE = "public_chat.turn"
DEFER_DELAY_SECONDS = 1
# A pending turn this old with nobody working on it is re-dispatched when the
# visitor polls (covers a lost in-process task, e.g. an instance restart).
PENDING_GRACE_SECONDS = 3
RECOVERY_PAGE_SIZE = 200
QUEUED_POLL_MAX_MS = 5000
# Start-up recovery is retried (the database may still be waking up).
STARTUP_RECOVERY_BACKOFF_SECONDS = (0, 5, 30, 120, 600)


class TurnDispatcher:
    def __init__(
        self, *, max_concurrency: int, per_tenant: int,
        job_type: str = PUBLIC_CHAT_TURN_JOB_TYPE,
    ) -> None:
        self.job_type = job_type
        self._max_concurrency = max_concurrency
        self._per_tenant = per_tenant
        self._global: asyncio.Semaphore | None = None
        self._tenants: dict[int, asyncio.Semaphore] = {}
        # job id -> the task currently responsible for it (task-aware, so a
        # task that re-submits its own job never drops the successor's mark).
        self._inflight: dict[int, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.completed = 0
        self.failed = 0

    # -- submission -------------------------------------------------------

    def submit(self, job_id: int, organization_id: int, *, delay: float = 0.0) -> bool:
        """Schedule a turn; ``False`` if this process already has it in hand."""
        if job_id in self._inflight:
            return False
        if self.job_type == "handoff.notify" and len(self._inflight) >= 32:
            return False
        task = asyncio.get_running_loop().create_task(
            self._run(job_id, organization_id, delay)
        )
        self._inflight[job_id] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    def recover(self, jobs: Iterable[IntegrationJob]) -> int:
        """Re-submit open turns that nobody in this process is running and
        that look stalled. Cheap: no query, uses rows the caller already has."""
        now = datetime.now(UTC)
        submitted = 0
        for job in jobs:
            if job.id in self._inflight or job.organization_id is None:
                continue
            if job.status == "processing":
                locked = job.locked_at
                if locked is None or (now - locked).total_seconds() < (
                    settings.public_chat_turn_stale_seconds
                ):
                    continue
            else:
                age = (now - job.created_at).total_seconds() if job.created_at else 0
                due = job.available_at is None or job.available_at <= now
                if not due or age < PENDING_GRACE_SECONDS:
                    continue
            submitted += int(self.submit(job.id, job.organization_id))
        if submitted:
            log.info("public_chat_turns_recovered", count=submitted)
        return submitted

    @staticmethod
    def _claimable_in(job: IntegrationJob, now: datetime) -> float:
        """Seconds until ``claim_by_id`` could succeed for this job."""
        stale = settings.public_chat_turn_stale_seconds
        if job.status == "processing":
            if job.locked_at is None:
                return 0.0
            age = (now - job.locked_at).total_seconds()
            # Possibly still running elsewhere (another instance, or this
            # instance before a restart): re-check right after the claim
            # turns stale. If it finished meanwhile, the claim simply fails.
            return max(0.0, stale - age + 1.0)
        if job.available_at is not None and job.available_at > now:
            return (job.available_at - now).total_seconds()
        return 0.0

    async def recover_all(self, *, page_size: int = RECOVERY_PAGE_SIZE) -> int:
        """Schedule every unfinished turn (Phase 1P.9R).

        Walks the whole backlog in keyset pages (no 100-row ceiling) and
        schedules each turn for the moment it becomes claimable, so a turn
        that was mid-model-call when the process died is retried as soon as
        its claim goes stale - without a visitor polling and without
        periodic database polling.
        """
        after_id = 0
        scheduled = 0
        while True:
            async with AsyncSessionLocal() as db:
                jobs = await IntegrationJobRepository.list_open_by_type(
                    db,
                    job_type=self.job_type,
                    after_id=after_id,
                    limit=page_size,
                )
            now = datetime.now(UTC)
            for job in jobs:
                if job.organization_id is not None:
                    scheduled += int(
                        self.submit(
                            job.id, job.organization_id, delay=self._claimable_in(job, now)
                        )
                    )
            if len(jobs) < page_size:
                return scheduled
            after_id = jobs[-1].id

    async def recover_on_startup(self) -> int:
        """Resume jobs plus reconcile missed initial handoffs after activation."""
        if self.job_type == "handoff.notify":
            from app.services.handoff_notification_service import reconcile_missing_handoffs

            async with AsyncSessionLocal() as db:
                repaired = await reconcile_missing_handoffs(db)
            for job_id, org in repaired:
                self.submit(job_id, org)
        return await self.recover_all()

    async def run_maintenance(self) -> None:
        """Lifespan task: start-up recovery (retried until it succeeds) and
        then a low-frequency sweep for turns nobody is waiting on.

        The sweep is one keyset-paged query per ``public_chat_turn_sweep_seconds``
        (hourly by default) - far from continuous polling, so Neon's
        scale-to-zero still applies between sweeps.
        """
        for delay in STARTUP_RECOVERY_BACKOFF_SECONDS:
            if delay:
                await asyncio.sleep(delay)
            try:
                resumed = await self.recover_on_startup()
                log.info("public_chat_turns_resumed_on_startup", count=resumed)
                break
            except Exception as exc:  # noqa: BLE001 - retried, then the sweep
                log.warning(
                    "public_chat_turn_startup_recovery_failed",
                    error_type=type(exc).__name__,
                )
        interval = settings.public_chat_turn_sweep_seconds
        while interval > 0:
            await asyncio.sleep(interval)
            try:
                swept = await self.recover_all()
                if swept:
                    log.info("public_chat_turns_swept", count=swept)
            except Exception as exc:  # noqa: BLE001 - next interval retries
                log.warning("public_chat_turn_sweep_failed", error_type=type(exc).__name__)

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for every scheduled turn (tests, graceful shutdown)."""
        deadline = time.monotonic() + timeout
        while self._tasks and time.monotonic() < deadline:
            await asyncio.wait(set(self._tasks), timeout=max(0.0, deadline - time.monotonic()))

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    def queued_poll_after_ms(self) -> int:
        """Suggested visitor poll interval while a turn waits for a slot.

        Roughly one model call per "wave" of ``max_concurrency`` turns ahead,
        so with a long backlog each waiting visitor polls proportionally less
        often (1 s when idle, at most 5 s). Under a burst on a 0.5-CPU
        instance, fixed 1 s polling from every waiting visitor was itself the
        dominant CPU and connection load (Phase 1P.9R certification).
        """
        waves = -(-len(self._inflight) // max(1, self._max_concurrency))
        return int(min(QUEUED_POLL_MAX_MS, 1000 * max(1, waves)))

    # -- execution --------------------------------------------------------

    def _tenant_semaphore(self, organization_id: int) -> asyncio.Semaphore:
        semaphore = self._tenants.get(organization_id)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._per_tenant)
            self._tenants[organization_id] = semaphore
        return semaphore

    async def _run(self, job_id: int, organization_id: int, delay: float) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            if self._global is None:
                self._global = asyncio.Semaphore(self._max_concurrency)
            async with self._tenant_semaphore(organization_id), self._global:
                await self._process(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("public_chat_turn_dispatch_error", job_id=job_id)
        finally:
            if self._inflight.get(job_id) is asyncio.current_task():
                del self._inflight[job_id]

    async def _process(self, job_id: int) -> None:
        async with AsyncSessionLocal() as db:
            job = await IntegrationJobRepository.claim_by_id(
                db,
                job_id=job_id,
                job_type=self.job_type,
                stale_after_seconds=settings.public_chat_turn_stale_seconds,
            )
            if job is None:
                return  # someone else has it, or it is finished
            claimed_at = job.locked_at
            organization_id = int(job.organization_id or 0)
            queued_ms = (
                round((claimed_at - job.created_at).total_seconds() * 1000, 1)
                if claimed_at is not None and job.created_at is not None
                else None
            )
            started = time.perf_counter()
            try:
                execution = await IntegrationJobService.execute(db=db, job=job)
                if execution == EXECUTE_CLAIM_LOST:
                    return
                completed = await IntegrationJobRepository.mark_completed(
                    db, job_id=job_id, claimed_at=claimed_at
                )
                if completed is not None:
                    self.completed += 1
                    log.info(
                        "public_chat_turn_completed",
                        job_id=job_id,
                        organization_id=organization_id,
                        queued_ms=queued_ms,
                        processing_ms=round((time.perf_counter() - started) * 1000, 1),
                    )
            except JobDeferredError as exc:
                await db.rollback()
                deferred = await IntegrationJobRepository.defer_claimed(
                    db,
                    job_id=job_id,
                    claimed_at=claimed_at,
                    delay_seconds=DEFER_DELAY_SECONDS,
                    reason=str(exc),
                )
                if deferred is not None:
                    self._resubmit_later(job_id, organization_id, DEFER_DELAY_SECONDS)
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                failed = await IntegrationJobRepository.mark_failed(
                    db,
                    job_id=job_id,
                    error_message=type(exc).__name__,
                    claimed_at=claimed_at,
                    terminal=isinstance(exc, PermanentJobError),
                )
                if failed is None:
                    return
                if failed.status == "retry":
                    delay = max(
                        0.0, (failed.available_at - datetime.now(UTC)).total_seconds()
                    )
                    self._resubmit_later(job_id, organization_id, delay)
                    log.warning(
                        "public_chat_turn_retry",
                        job_id=job_id,
                        organization_id=organization_id,
                        error_type=type(exc).__name__,
                    )
                else:
                    self.failed += 1
                    await IntegrationJobService.handle_failure(
                        db=db, job=failed, error_message=type(exc).__name__
                    )
                    log.error(
                        "public_chat_turn_failed",
                        job_id=job_id,
                        organization_id=organization_id,
                        error_type=type(exc).__name__,
                    )

    def _resubmit_later(self, job_id: int, organization_id: int, delay: float) -> None:
        # Hand the job to a fresh task; the current task's ``finally`` sees the
        # successor registered and leaves its marker alone.
        self._inflight.pop(job_id, None)
        self.submit(job_id, organization_id, delay=delay)


turn_dispatcher = TurnDispatcher(
    max_concurrency=settings.public_chat_turn_max_concurrency,
    per_tenant=settings.public_chat_turn_max_concurrency_per_tenant,
)

# Same tested claim/recovery implementation, but bounded to 2 global and 1
# per tenant for notification transport. No extra Render worker process.
handoff_dispatcher = TurnDispatcher(
    max_concurrency=2, per_tenant=1, job_type="handoff.notify"
)
