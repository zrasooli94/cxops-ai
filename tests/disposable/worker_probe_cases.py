"""Actual worker + PostgreSQL claim semantics with NO real provider access.

Explicitly excluded from normal pytest filename discovery. Only the dedicated
CI lane may import this module; it requires an exact disposable localhost DB.
The real worker and repository perform the claims/commits/fenced writes. Only
external service execution and expiry candidates are replaced by inert fakes.
"""
from __future__ import annotations

import os

from scripts.worker_disposable_guard import require_disposable_environment

# This runs before loading the application, job models or integration clients.
require_disposable_environment(os.environ)

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.repositories.integration_job_repository import (
    STALE_EXHAUSTED_ERROR,
    IntegrationJobRepository,
)
from app.services.public_chat_expiry_service import ExpiryBatchResult
from scripts import worker

JOB_TYPE = "synthetic.worker.probe"
ORIGINAL_SLA_SCAN = worker.run_sla_scan_cycle


@pytest_asyncio.fixture
async def isolated_worker(monkeypatch: pytest.MonkeyPatch):
    """Only local disposable PostgreSQL may be used by worker module."""
    url = require_disposable_environment(os.environ)
    engine = create_async_engine(url, poolclass=NullPool, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "AsyncSessionLocal", maker)
    monkeypatch.setattr(worker, "engine", engine)

    async def _scan() -> str:
        return worker.SLA_SCAN_COMPLETED

    async def _forbid_external(*, db: Any, job: IntegrationJob) -> None:
        raise AssertionError("No provider executor was authorized")

    async def _no_external_failure(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(worker, "run_sla_scan_cycle", _scan)
    monkeypatch.setattr(
        worker.IntegrationJobService, "execute", staticmethod(_forbid_external)
    )
    monkeypatch.setattr(
        worker.IntegrationJobService,
        "handle_failure",
        staticmethod(_no_external_failure),
    )
    org_ids: list[int] = []

    async def seed(count: int) -> tuple[list[int], list[int]]:
        namespace = uuid.uuid4().hex
        async with maker() as db:
            orgs = [
                Organization(name=f"probe-{namespace}-alpha"),
                Organization(name=f"probe-{namespace}-beta"),
            ]
            db.add_all(orgs)
            await db.flush()
            ids = [org.id for org in orgs]
            org_ids.extend(ids)
            jobs = [
                IntegrationJob(
                    dedupe_key=f"probe:{namespace}:{index}",
                    job_type=JOB_TYPE,
                    organization_id=ids[index % len(ids)],
                    payload={"synthetic": True, "sequence": index},
                    status="pending",
                    attempts=0,
                    max_attempts=3,
                    available_at=datetime.now(UTC) - timedelta(seconds=5),
                )
                for index in range(count)
            ]
            db.add_all(jobs)
            await db.flush()
            job_ids = [job.id for job in jobs]
            await db.commit()
            return ids, job_ids

    try:
        yield maker, engine, seed
    finally:
        if org_ids:
            async with maker() as db:
                await db.execute(
                    delete(IntegrationJob).where(
                        IntegrationJob.organization_id.in_(org_ids)
                    )
                )
                await db.execute(
                    delete(Organization).where(Organization.id.in_(org_ids))
                )
                await db.commit()
        await engine.dispose()


async def _job_rows(maker: Any, ids: list[int]) -> list[IntegrationJob]:
    async with maker() as db:
        rows = await db.execute(
            select(IntegrationJob)
            .where(IntegrationJob.id.in_(ids))
            .order_by(IntegrationJob.id.asc())
        )
        return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_two_real_worker_drains_claim_each_synthetic_job_once(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker, _engine, seed = isolated_worker
    organizations, ids = await seed(24)
    seen: list[int] = []
    expiry_calls: list[int] = []

    async def _synthetic_execution(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        assert job.payload.get("synthetic") is True
        assert job.organization_id in organizations
        seen.append(job.id)
        await asyncio.sleep(0.005)


    async def _synthetic_expiry(
        _session_factory: Any, *, limit: int, **_kwargs: Any
    ) -> ExpiryBatchResult:
        expiry_calls.append(limit)
        return ExpiryBatchResult(limit=limit)

    monkeypatch.setattr(
        worker.IntegrationJobService,
        "execute",
        staticmethod(_synthetic_execution),
    )
    monkeypatch.setattr(
        worker.PublicChatExpiryService,
        "finalize_expired_batch",
        staticmethod(_synthetic_expiry),
    )
    summaries = await asyncio.wait_for(
        asyncio.gather(
            *[
                worker.run_worker_once(
                    max_jobs=20,
                    max_seconds=60,
                    finalize_expired_public_chat=True,
                    expiry_batch_size=50,
                )
                for _ in range(2)
            ]
        ),
        timeout=45,
    )
    rows = await _job_rows(maker, ids)
    assert sum(s["jobs_processed"] for s in summaries) == 24
    assert len(seen) == 24
    assert Counter(seen) == Counter(ids)
    assert all(job.status == "completed" for job in rows)
    assert all(job.attempts == 1 and job.locked_at is None for job in rows)
    assert {job.organization_id for job in rows} == set(organizations)
    assert expiry_calls == [50, 50]
    assert all(s["health"] == "healthy" for s in summaries)
    assert all(s["expiry_outcome"] == "ok" for s in summaries)


@pytest.mark.asyncio
async def test_transient_provider_free_failure_retries_then_completes(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker, _engine, seed = isolated_worker
    _orgs, ids = await seed(1)
    attempts: list[int] = []

    async def _synthetic_flaky(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        attempts.append(job.attempts)
        if len(attempts) == 1:
            raise RuntimeError("synthetic transient error")


    monkeypatch.setattr(
        worker.IntegrationJobService, "execute", staticmethod(_synthetic_flaky)
    )
    first = await worker.run_worker_once(max_jobs=1, max_seconds=30)
    row = (await _job_rows(maker, ids))[0]
    assert first["health"] == "unsuccessful"
    assert row.status == "retry"
    assert row.attempts == 1
    assert row.locked_at is None
    assert row.available_at > datetime.now(UTC)

    # Move only this disposable row's retry time forward; no real waiting.
    async with maker() as db:
        await db.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == ids[0])
            .values(available_at=datetime.now(UTC) - timedelta(seconds=5))
        )
        await db.commit()
    second = await worker.run_worker_once(max_jobs=1, max_seconds=30)
    row = (await _job_rows(maker, ids))[0]
    assert second["health"] == "healthy"
    assert row.status == "completed" and row.attempts == 2
    assert attempts == [1, 2]


@pytest.mark.asyncio
async def test_permanent_job_failure_is_terminal_with_one_attempt(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker, _engine, seed = isolated_worker
    _orgs, ids = await seed(1)

    async def _bad_job(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        raise worker.PermanentJobError("synthetic invalid payload")

    monkeypatch.setattr(
        worker.IntegrationJobService, "execute", staticmethod(_bad_job)
    )
    first = await worker.run_worker_once(max_jobs=2, max_seconds=30)
    row = (await _job_rows(maker, ids))[0]
    assert first["health"] == "unsuccessful"
    assert row.status == "failed" and row.attempts == 1
    assert row.locked_at is None
    empty = await worker.run_worker_once(max_jobs=2, max_seconds=30)
    assert empty["jobs_processed"] == 0
    assert (await _job_rows(maker, ids))[0].status == "failed"


@pytest.mark.asyncio
async def test_real_repository_recovers_stale_and_exhausts_poison_pill(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker, _engine, seed = isolated_worker
    _orgs, ids = await seed(2)
    async with maker() as db:
        for index, id_ in enumerate(ids):
            await db.execute(
                update(IntegrationJob)
                .where(IntegrationJob.id == id_)
                .values(
                    status="processing",
                    attempts=1 if index == 0 else 3,
                    locked_at=datetime.now(UTC) - timedelta(minutes=15),
                )
            )
        await db.commit()
    seen: list[int] = []

    async def _safe_job(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        seen.append(job.id)


    monkeypatch.setattr(
        worker.IntegrationJobService, "execute", staticmethod(_safe_job)
    )
    result = await worker.run_worker_once(max_jobs=10, max_seconds=30)
    rows = await _job_rows(maker, ids)
    assert seen == [ids[0]]
    assert result["jobs_processed"] == 1
    assert rows[0].status == "completed" and rows[0].attempts == 2
    assert rows[1].status == "failed" and rows[1].attempts == 3
    assert rows[1].last_error == STALE_EXHAUSTED_ERROR


@pytest.mark.asyncio
async def test_stale_claim_token_cannot_complete_a_newer_claim(
    isolated_worker: Any
) -> None:
    maker, _engine, seed = isolated_worker
    _orgs, ids = await seed(1)
    async with maker() as db:
        job = await IntegrationJobRepository.claim_next_unscoped(db)
        assert job is not None and job.id == ids[0]
        old_claim = job.locked_at
        assert old_claim is not None
        # A simulated new owner took over this disposable job. Test the real
        # atomic fenced update rather than trusting a Python-only condition.
        await db.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == ids[0])
            .values(locked_at=old_claim + timedelta(minutes=1))
        )
        await db.commit()

    async with maker() as db:
        rejected = await IntegrationJobRepository.mark_completed(
            db, job_id=ids[0], claimed_at=old_claim
        )
    assert rejected is None
    current = (await _job_rows(maker, ids))[0]
    assert current.status == "processing"
    assert current.locked_at != old_claim


@pytest.mark.asyncio
async def test_real_postgres_advisory_scanner_lock_rejects_overlap(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _maker, _engine, _seed = isolated_worker
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def _hold_scan(_cls: Any, _db: Any, *, now: datetime) -> tuple[int, int]:
        entered.set()
        await asyncio.wait_for(finish.wait(), 5)
        return 0, 0

    monkeypatch.setattr(
        worker.SLAEscalationScannerService, "scan_once", classmethod(_hold_scan)
    )
    first = asyncio.create_task(ORIGINAL_SLA_SCAN())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second_result = await asyncio.wait_for(ORIGINAL_SLA_SCAN(), 5)
        assert second_result == worker.SLA_SCAN_SKIPPED_LOCKED
    finally:
        finish.set()
        first_result = await asyncio.wait_for(first, 5)
    assert first_result == worker.SLA_SCAN_COMPLETED


# ---------------------------------------------------------------------------
# Representative synthetic load; the real PostgreSQL claimant and one-shot
# worker run unchanged. Only remote provider operations are intentionally faked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulated_backlog_two_workers_with_real_claims(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """65 multi-tenant jobs, two overlapping workers, then bounded catch-up."""
    maker, _engine, seed = isolated_worker
    organizations, ids = await seed(65)
    processed: list[int] = []

    async def _slow_fake_provider(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        assert job.organization_id in organizations
        assert job.payload.get("synthetic") is True
        processed.append(job.id)
        await asyncio.sleep(0.012)

    monkeypatch.setattr(
        worker.IntegrationJobService,
        "execute",
        staticmethod(_slow_fake_provider),
    )
    first_two = await asyncio.wait_for(
        asyncio.gather(
            worker.run_worker_once(max_jobs=25, max_seconds=30),
            worker.run_worker_once(max_jobs=25, max_seconds=30),
        ),
        timeout=35,
    )
    remaining = await worker.run_worker_once(max_jobs=25, max_seconds=30)
    summaries = [*first_two, remaining]
    rows = await _job_rows(maker, ids)

    assert [summary["jobs_processed"] for summary in first_two] == [25, 25]
    assert remaining["jobs_processed"] == 15
    assert all(summary["jobs_processed"] <= 25 for summary in summaries)
    assert len(processed) == 65
    assert Counter(processed) == Counter(ids), "no duplicate provider invocation"
    assert {job.organization_id for job in rows} == set(organizations)
    assert all(job.status == "completed" for job in rows)
    assert all(job.attempts == 1 and job.locked_at is None for job in rows)
    assert all(summary["health"] == worker.RUN_HEALTHY for summary in summaries)


@pytest.mark.asyncio
async def test_simulated_slow_provider_finishes_claim_before_deadline_exit(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-shot deadline must be checked between, not inside, claims."""
    maker, _engine, seed = isolated_worker
    _orgs, ids = await seed(4)
    seen: list[int] = []

    async def _bounded_provider(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        seen.append(job.id)
        await asyncio.sleep(0.55)

    monkeypatch.setattr(
        worker.IntegrationJobService,
        "execute",
        staticmethod(_bounded_provider),
    )
    first = await worker.run_worker_once(max_jobs=10, max_seconds=0.30)
    assert first["jobs_processed"] == 1
    assert first["exit_reason"] == "max_seconds"
    rows = await _job_rows(maker, ids)
    assert sum(j.status == "completed" for j in rows) == 1
    assert sum(j.status == "pending" for j in rows) == 3
    assert all(j.status != "processing" for j in rows)

    async def _recovered_provider(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        seen.append(job.id)

    monkeypatch.setattr(
        worker.IntegrationJobService,
        "execute",
        staticmethod(_recovered_provider),
    )
    second = await worker.run_worker_once(max_jobs=10, max_seconds=15)
    assert second["jobs_processed"] == 3
    assert Counter(seen) == Counter(ids)
    assert all(j.status == "completed" for j in await _job_rows(maker, ids))


@pytest.mark.asyncio
async def test_simulated_provider_failure_storm_retry_recovery(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20 independent transient failures, real backoff/fencing, eventual recovery."""
    maker, _engine, seed = isolated_worker
    _orgs, ids = await seed(20)
    attempts: dict[int, list[int]] = {job_id: [] for job_id in ids}

    async def _flaky_provider(*, db: Any, job: IntegrationJob) -> None:
        assert job.job_type == JOB_TYPE
        attempts[job.id].append(job.attempts)
        if job.attempts == 1:
            raise RuntimeError("synthetic provider temporary outage")

    monkeypatch.setattr(
        worker.IntegrationJobService,
        "execute",
        staticmethod(_flaky_provider),
    )
    for _ in range(6):
        await worker.run_worker_once(max_jobs=20, max_seconds=25)
        # Advance ONLY synthetic, disposable retry timestamps. Never sleep
        # minutes or change the production retry/backoff implementation.
        async with maker() as db:
            await db.execute(
                update(IntegrationJob)
                .where(
                    IntegrationJob.id.in_(ids),
                    IntegrationJob.status == "retry",
                )
                .values(available_at=datetime.now(UTC) - timedelta(seconds=2))
            )
            await db.commit()
        rows = await _job_rows(maker, ids)
        if all(row.status == "completed" for row in rows):
            break
    else:
        pytest.fail("bounded synthetic retry-storm did not recover")

    assert len(attempts) == 20
    assert all(history == [1, 2] for history in attempts.values())
    assert all(row.attempts == 2 and row.locked_at is None for row in rows)
    assert all(row.status == "completed" for row in rows)


@pytest.mark.asyncio
async def test_simulated_cancellation_reclaimed_only_after_stale_fence(
    isolated_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abrupt task cancellation leaves no false completion; stale reclaim works."""
    maker, _engine, seed = isolated_worker
    _orgs, ids = await seed(1)
    entered = asyncio.Event()
    held = asyncio.Event()
    finished: list[int] = []

    async def _blocking_fake_provider(*, db: Any, job: IntegrationJob) -> None:
        assert job.id == ids[0]
        entered.set()
        await held.wait()  # cancelled by test: no provider work or socket

    monkeypatch.setattr(
        worker.IntegrationJobService,
        "execute",
        staticmethod(_blocking_fake_provider),
    )
    task = asyncio.create_task(
        worker.run_worker_once(max_jobs=1, max_seconds=10)
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)

    row = (await _job_rows(maker, ids))[0]
    assert row.status == "processing"
    assert row.attempts == 1
    assert row.locked_at is not None
    assert finished == [], "cancelled provider never completed"

    # Only accelerate this synthetic row beyond the real ten-minute fence.
    async with maker() as db:
        await db.execute(
            update(IntegrationJob)
            .where(IntegrationJob.id == ids[0])
            .values(locked_at=datetime.now(UTC) - timedelta(minutes=15))
        )
        await db.commit()

    async def _working_fake_provider(*, db: Any, job: IntegrationJob) -> None:
        assert job.id == ids[0]
        finished.append(job.id)

    monkeypatch.setattr(
        worker.IntegrationJobService,
        "execute",
        staticmethod(_working_fake_provider),
    )
    summary = await worker.run_worker_once(max_jobs=1, max_seconds=15)
    current = (await _job_rows(maker, ids))[0]
    assert summary["jobs_processed"] == 1
    assert summary["health"] == worker.RUN_HEALTHY
    assert current.status == "completed" and current.attempts == 2
    assert finished == ids
