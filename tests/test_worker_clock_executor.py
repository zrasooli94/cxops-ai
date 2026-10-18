from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.worker_clock_tick import WorkerClockTick
from app.repositories.worker_clock_repository import WorkerClockRepository
from scripts import worker_clock_executor


@pytest_asyncio.fixture(autouse=True)
async def _clean_ticks(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    await db.execute(delete(WorkerClockTick))
    await db.commit()
    monkeypatch.setattr(settings, "worker_clock_id", "executor-test")


async def _record(db: AsyncSession, slot: int = 6000000) -> None:
    await WorkerClockRepository.record_tick(
        db, clock_id="executor-test", slot=slot, max_catchup_slots=12
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_global_lease_allows_exactly_one_concurrent_worker_pass(
    db: AsyncSession,
) -> None:
    await _record(db)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def run(**_kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"health": "healthy"}

    first = asyncio.create_task(worker_clock_executor.execute_one_tick(worker_run=run))
    await entered.wait()
    second = await worker_clock_executor.execute_one_tick(worker_run=run)
    release.set()
    assert await first == "completed"
    assert second == "busy"
    assert calls == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_missed_ticks_produce_one_bounded_pass(db: AsyncSession) -> None:
    await _record(db, 100)
    await _record(db, 103)
    calls = 0

    async def run(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["max_jobs"] == 7
        assert kwargs["max_seconds"] == 30
        return {"health": "healthy"}

    assert (
        await worker_clock_executor.execute_one_tick(
            worker_run=run, max_jobs=7, max_seconds=30
        )
        == "completed"
    )
    assert await worker_clock_executor.execute_one_tick(worker_run=run) == "idle"
    assert calls == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_claim_token_cannot_finalize_recovered_claim(
    db: AsyncSession,
) -> None:
    await _record(db)
    old = await WorkerClockRepository.claim_next(db, clock_id="executor-test")
    assert old is not None and old.claim_token
    old_token = old.claim_token
    await db.execute(
        update(WorkerClockTick)
        .where(WorkerClockTick.id == old.id)
        .values(
            status="processing",
            attempts=1,
            locked_at=datetime.now(UTC) - timedelta(seconds=601),
        )
    )
    await db.commit()
    current = await WorkerClockRepository.claim_next(db, clock_id="executor-test")
    assert current is not None and current.claim_token != old_token
    assert await WorkerClockRepository.finish_claim(
        db,
        tick_id=old.id,
        claim_token=old_token,
        succeeded=True,
    ) is None
    assert await WorkerClockRepository.finish_claim(
        db,
        tick_id=current.id,
        claim_token=current.claim_token or "",
        succeeded=True,
    ) == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_worker_is_retryable_without_raw_error(db: AsyncSession) -> None:
    await _record(db)

    class SecretBearingFailure(RuntimeError):
        pass

    async def run(**_kwargs):
        raise SecretBearingFailure("credential=must-not-persist")

    assert await worker_clock_executor.execute_one_tick(worker_run=run) == "retry"
    row = (await db.execute(select(WorkerClockTick))).scalar_one()
    assert row.status == "retry"
    assert row.last_error == "SecretBearingFailure"


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_worker_stops_exactly_at_max_attempts(db: AsyncSession) -> None:
    await _record(db)
    calls = 0

    async def run(**_kwargs):
        nonlocal calls
        calls += 1
        return {"health": worker_clock_executor.RUN_UNSUCCESSFUL}

    assert await worker_clock_executor.execute_one_tick(worker_run=run) == "retry"
    assert await worker_clock_executor.execute_one_tick(worker_run=run) == "retry"
    assert await worker_clock_executor.execute_one_tick(worker_run=run) == "failed"
    assert await worker_clock_executor.execute_one_tick(worker_run=run) == "idle"
    row = (await db.execute(select(WorkerClockTick))).scalar_one()
    assert row.status == "failed"
    assert row.attempts == row.max_attempts == 3
    assert calls == 3


def test_cli_disabled_fails_before_preflight_or_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "worker_clock_executor_enabled", False)
    monkeypatch.setattr(
        worker_clock_executor,
        "_require_compatible_schema",
        lambda: (_ for _ in ()).throw(AssertionError("schema gate ran")),
    )
    assert worker_clock_executor.main([]) == 2


def test_production_preflight_failure_claims_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "worker_clock_executor_enabled", True)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(worker_clock_executor, "run_production_preflight", lambda: 1)
    monkeypatch.setattr(
        worker_clock_executor,
        "_require_compatible_schema",
        lambda: (_ for _ in ()).throw(AssertionError("schema gate ran")),
    )
    assert worker_clock_executor.main([]) == 1
