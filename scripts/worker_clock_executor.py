"""Disabled, bounded executor for durable worker-clock intents.

This command is not scheduled or started by the API. A later deployment design
must provide a reliable invoker; source inclusion alone activates nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.repositories.worker_clock_repository import WorkerClockRepository
from scripts.preflight_production_env import run as run_production_preflight
from scripts.worker import (
    DEFAULT_ONCE_MAX_JOBS,
    DEFAULT_ONCE_MAX_SECONDS,
    RUN_UNSUCCESSFUL,
    _require_compatible_schema,
    run_worker_once,
)

GLOBAL_CLOCK_EXECUTOR_LOCK_KEY = 0x434C_4F43_4B45_5845


async def execute_one_tick(
    *,
    worker_run: Callable[..., Awaitable[dict[str, Any]]] = run_worker_once,
    max_jobs: int = DEFAULT_ONCE_MAX_JOBS,
    max_seconds: float = DEFAULT_ONCE_MAX_SECONDS,
) -> str:
    """Execute at most one coalesced tick while holding the global DB lease."""
    async with engine.connect() as lease_connection:
        acquired = bool(
            (
                await lease_connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": GLOBAL_CLOCK_EXECUTOR_LOCK_KEY},
                )
            ).scalar()
        )
        if not acquired:
            return "busy"
        try:
            async with AsyncSessionLocal() as db:
                tick = await WorkerClockRepository.claim_next(
                    db, clock_id=settings.worker_clock_id
                )
            if tick is None:
                return "idle"

            token = tick.claim_token
            if token is None:
                raise RuntimeError("claimed clock tick has no fencing token")
            try:
                summary = await worker_run(
                    max_jobs=max_jobs,
                    max_seconds=max_seconds,
                    finalize_expired_public_chat=True,
                )
                succeeded = summary.get("health") != RUN_UNSUCCESSFUL
                error_category = None if succeeded else "WorkerRunUnsuccessful"
            except Exception as exc:  # noqa: BLE001
                succeeded = False
                error_category = type(exc).__name__

            async with AsyncSessionLocal() as db:
                final_status = await WorkerClockRepository.finish_claim(
                    db,
                    tick_id=tick.id,
                    claim_token=token,
                    succeeded=succeeded,
                    error_category=error_category,
                )
            if final_status is None:
                return "claim_lost"
            return final_status
        finally:
            await lease_connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": GLOBAL_CLOCK_EXECUTOR_LOCK_KEY},
            )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.worker_clock_executor")
    parser.add_argument("--max-jobs", type=int, default=DEFAULT_ONCE_MAX_JOBS)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_ONCE_MAX_SECONDS)
    args = parser.parse_args(argv)
    if args.max_jobs < 1:
        parser.error("--max-jobs must be >= 1")
    if args.max_seconds <= 0:
        parser.error("--max-seconds must be > 0")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not settings.worker_clock_executor_enabled:
        print("worker clock executor is disabled")
        return 2
    if settings.environment == "production" and run_production_preflight() != 0:
        return 1
    _require_compatible_schema()
    outcome = asyncio.run(
        execute_one_tick(max_jobs=args.max_jobs, max_seconds=args.max_seconds)
    )
    return 0 if outcome in {"completed", "idle", "busy"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
