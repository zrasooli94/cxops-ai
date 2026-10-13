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
when the waiting visitor polls for updates and (b) once at API start-up. The
scheduled worker processes anything left as a backstop. A turn that still fails
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


class TurnDispatcher:
    def __init__(self, *, max_concurrency: int, per_tenant: int) -> None:
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

    async def recover_on_startup(self, *, limit: int = 100) -> int:
        """Resume turns interrupted by a restart. Bounded, one query."""
        async with AsyncSessionLocal() as db:
            jobs = await IntegrationJobRepository.list_recoverable(
                db,
                job_type=PUBLIC_CHAT_TURN_JOB_TYPE,
                stale_after_seconds=settings.public_chat_turn_stale_seconds,
                limit=limit,
            )
        count = 0
        for job in jobs:
            if job.organization_id is not None:
                count += int(self.submit(job.id, job.organization_id))
        return count

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for every scheduled turn (tests, graceful shutdown)."""
        deadline = time.monotonic() + timeout
        while self._tasks and time.monotonic() < deadline:
            await asyncio.wait(set(self._tasks), timeout=max(0.0, deadline - time.monotonic()))

    @property
    def inflight(self) -> int:
        return len(self._inflight)

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
                job_type=PUBLIC_CHAT_TURN_JOB_TYPE,
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
