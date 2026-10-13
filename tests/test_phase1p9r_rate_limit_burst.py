"""Phase 1P.9R — the DB rate limiter under a burst on one widget.

Before: concurrent requests that raced to create a window's bucket were all
rejected except the winner (INSERT ... ON CONFLICT DO NOTHING -> rowcount 0),
so 10 simultaneous visitors on one site got 9 "widget is busy" errors far
below the limit. Now one atomic upsert admits exactly ``limit`` requests.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.services.public_chat_rate_limiter import public_chat_rate_limiter_db


async def _hit(key: str, limit: int, now_epoch: float) -> bool:
    async with AsyncSessionLocal() as session:
        allowed = await public_chat_rate_limiter_db.allow(
            session,
            scope="config_messages",
            key=key,
            limit=limit,
            window_seconds=60.0,
            now_epoch=now_epoch,
        )
        await session.commit()
        return allowed


@pytest.mark.parametrize(("limit", "burst", "admitted"), [(600, 10, 10), (3, 10, 3), (1, 5, 1)])
@pytest.mark.asyncio
async def test_burst_on_a_fresh_window_admits_exactly_the_limit(limit, burst, admitted):
    key = f"burst-test:{uuid.uuid4().hex}"
    now = 4_102_444_800.0  # a fixed future window no other test touches
    results = await asyncio.gather(*(_hit(key, limit, now) for _ in range(burst)))
    assert sum(results) == admitted


@pytest.mark.asyncio
async def test_next_window_starts_from_zero():
    key = f"burst-test:{uuid.uuid4().hex}"
    now = 4_102_444_800.0
    assert await _hit(key, 1, now)
    assert not await _hit(key, 1, now + 1)
    assert await _hit(key, 1, now + 61)
