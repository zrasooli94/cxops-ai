from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.worker_clock_tick import WorkerClockTick

SLOT_SECONDS = 300
STALE_TICK_CLAIM_SECONDS = 600
STALE_CLAIM_ERROR = "Recovered abandoned clock execution"
EXHAUSTED_CLAIM_ERROR = "Abandoned clock execution exhausted attempts"


@dataclass(frozen=True)
class RecordedTick:
    accepted: bool
    inserted_slots: int
    recovered_slots: int
    catchup_truncated: bool


class WorkerClockRepository:
    """Global scheduling ledger. It is internal-only and contains no tenant data."""

    @staticmethod
    async def record_tick(
        db: AsyncSession,
        *,
        clock_id: str,
        slot: int,
        max_catchup_slots: int,
    ) -> RecordedTick:
        if not clock_id or not 1 <= len(clock_id) <= 80:
            raise ValueError("invalid clock id")
        if type(slot) is not int or slot <= 0:
            raise ValueError("invalid slot")
        if not 0 <= max_catchup_slots <= 288:
            raise ValueError("invalid catchup bound")

        # Serialize gap calculation per clock. The unique constraint is still
        # authoritative for dedupe; this lock makes concurrent catch-up ranges
        # deterministic and avoids needlessly contending inserts.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:clock_id, 0))"),
            {"clock_id": clock_id},
        )
        latest = (
            await db.execute(
                select(func.max(WorkerClockTick.slot)).where(
                    WorkerClockTick.clock_id == clock_id
                )
            )
        ).scalar_one()

        catchup_truncated = False
        if latest is None or slot <= latest:
            slots = [slot]
        else:
            first_missing = latest + 1
            bounded_first = max(first_missing, slot - max_catchup_slots)
            catchup_truncated = bounded_first > first_missing
            slots = list(range(bounded_first, slot + 1))

        statement = (
            insert(WorkerClockTick)
            .values(
                [
                    {
                        "clock_id": clock_id,
                        "slot": candidate,
                        "scheduled_at": datetime.fromtimestamp(
                            candidate * SLOT_SECONDS, tz=UTC
                        ),
                    }
                    for candidate in slots
                ]
            )
            .on_conflict_do_nothing(constraint="uq_worker_clock_ticks_clock_slot")
            .returning(WorkerClockTick.slot)
        )
        inserted = {row[0] for row in (await db.execute(statement)).all()}
        await db.commit()
        return RecordedTick(
            accepted=slot in inserted,
            inserted_slots=len(inserted),
            recovered_slots=len(inserted - {slot}),
            catchup_truncated=catchup_truncated,
        )

    @staticmethod
    async def claim_next(
        db: AsyncSession,
        *,
        clock_id: str,
        now: datetime | None = None,
    ) -> WorkerClockTick | None:
        current = now or (await db.execute(select(func.now()))).scalar_one()
        stale_before = current - timedelta(seconds=STALE_TICK_CLAIM_SECONDS)

        await db.execute(
            update(WorkerClockTick)
            .where(
                WorkerClockTick.clock_id == clock_id,
                WorkerClockTick.status == "processing",
                WorkerClockTick.locked_at < stale_before,
                WorkerClockTick.attempts >= WorkerClockTick.max_attempts,
            )
            .values(
                status="failed",
                locked_at=None,
                claim_token=None,
                last_error=EXHAUSTED_CLAIM_ERROR,
            )
        )
        await db.execute(
            update(WorkerClockTick)
            .where(
                WorkerClockTick.clock_id == clock_id,
                WorkerClockTick.status == "processing",
                WorkerClockTick.locked_at < stale_before,
                WorkerClockTick.attempts < WorkerClockTick.max_attempts,
            )
            .values(
                status="retry",
                locked_at=None,
                claim_token=None,
                last_error=STALE_CLAIM_ERROR,
            )
        )

        # One worker pass drains the durable job queue, so executing one pass
        # for every missed five-minute tick would create a catch-up storm. Claim
        # only the newest intent and durably coalesce older pending intents.
        newest_slot = (
            await db.execute(
                select(func.max(WorkerClockTick.slot)).where(
                    WorkerClockTick.clock_id == clock_id,
                    WorkerClockTick.status.in_(("pending", "retry")),
                    WorkerClockTick.attempts < WorkerClockTick.max_attempts,
                )
            )
        ).scalar_one()
        if newest_slot is None:
            await db.commit()
            return None
        await db.execute(
            update(WorkerClockTick)
            .where(
                WorkerClockTick.clock_id == clock_id,
                WorkerClockTick.status.in_(("pending", "retry")),
                WorkerClockTick.slot < newest_slot,
            )
            .values(status="coalesced", completed_at=current, last_error=None)
        )

        tick = (
            await db.execute(
                select(WorkerClockTick)
                .where(
                    WorkerClockTick.clock_id == clock_id,
                    WorkerClockTick.slot == newest_slot,
                    WorkerClockTick.status.in_(("pending", "retry")),
                    WorkerClockTick.attempts < WorkerClockTick.max_attempts,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).scalar_one_or_none()
        if tick is None:
            await db.commit()
            return None

        tick.status = "processing"
        tick.attempts += 1
        tick.locked_at = current
        tick.claim_token = str(uuid4())
        tick.last_error = None
        await db.commit()
        await db.refresh(tick)
        return tick

    @staticmethod
    async def finish_claim(
        db: AsyncSession,
        *,
        tick_id: int,
        claim_token: str,
        succeeded: bool,
        error_category: str | None = None,
    ) -> str | None:
        values: dict[str, object] = {
            "status": "completed"
            if succeeded
            else case(
                (WorkerClockTick.attempts >= WorkerClockTick.max_attempts, "failed"),
                else_="retry",
            ),
            "locked_at": None,
            "claim_token": None,
            "last_error": None
            if succeeded
            else (error_category or "WorkerRunFailed")[:120],
        }
        if succeeded:
            values["completed_at"] = func.now()
        result = await db.execute(
            update(WorkerClockTick)
            .where(
                WorkerClockTick.id == tick_id,
                WorkerClockTick.status == "processing",
                WorkerClockTick.claim_token == claim_token,
            )
            .values(**values)
            .returning(WorkerClockTick.status)
        )
        await db.commit()
        row = result.first()
        return None if row is None else str(row[0])
