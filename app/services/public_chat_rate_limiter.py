"""Rate limits for the public chat API.

Phase 1P.1 ships a process-local sliding-window limiter (single-instance
topology); Phase 1P.2 adds a DB-backed fixed-window limiter so limits hold
across instances. Both are burst guards, not admission authorities — the
durable abuse guards are the per-session idempotent message write, the
per-config max-active-sessions count, and input bounds. False negatives
(over-limiting under concurrency) are safe.
"""

import hashlib
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.public_chat import PublicChatRateLimitBucket


@dataclass
class _Window:
    hits: deque[float] = field(default_factory=deque)

    def allow(self, limit: int, window_seconds: float, now: float) -> bool:
        while self.hits and self.hits[0] <= now - window_seconds:
            self.hits.popleft()
        if len(self.hits) >= limit:
            return False
        self.hits.append(now)
        return True


class PublicChatRateLimiter:
    """Sliding-window per-(scope, key) limiter with periodic cleanup (1P.1)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], _Window] = defaultdict(_Window)
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 600.0

    def allow(
        self,
        *,
        scope: str,
        key: str,
        limit: int,
        window_seconds: float,
    ) -> bool:
        now = time.monotonic()
        with self._lock:
            self._maybe_cleanup(now)
            return self._windows[(scope, key)].allow(limit, window_seconds, now)

    def _maybe_cleanup(self, now: float) -> None:
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        expired = [
            window_key
            for window_key, window in self._windows.items()
            if not window.hits or window.hits[-1] <= now - 3600.0
        ]
        for window_key in expired:
            del self._windows[window_key]


# Retained for back-compat; the service uses the DB-backed limiter.
public_chat_rate_limiter = PublicChatRateLimiter()

# Buckets whose window is older than this are pruned opportunistically.
_PRUNE_WINDOW_SECONDS = 7200.0


def _contract_key(scope: str, key: str) -> str:
    kernel = f"{scope}:{key}"
    if len(kernel) <= 255:
        return kernel
    return hashlib.sha256(kernel.encode("utf-8")).hexdigest()


class PublicChatRateLimiterDb:
    """DB-backed fixed-window per-(scope, key) limiter (Phase 1P.2).

    One row per ``(key_cache, window_start)`` aligned fixed bucket. A request
    is charged only when it is admitted (count is below the limit), so rejected
    requests never consume budget. Bounds are enforced atomically with a
    conditional increment, which stays correct across instances and under
    concurrency. Stale buckets are pruned opportunistically.
    """

    @classmethod
    def _window_start_epoch(cls, now_epoch: float, window_seconds: float) -> float:
        return now_epoch - (now_epoch % window_seconds)

    @classmethod
    async def allow(
        cls,
        db: AsyncSession,
        *,
        scope: str,
        key: str,
        limit: int,
        window_seconds: float,
        now_epoch: float | None = None,
    ) -> bool:
        if now_epoch is None:
            now_epoch = time.time()
        window_start = cls._window_start_epoch(now_epoch, window_seconds)
        window_ts = datetime.fromtimestamp(window_start, tz=UTC)
        kernel = _contract_key(scope, key)

        if limit < 1:
            raise ValueError("Rate limit must be at least 1.")

        result = await db.execute(
            update(PublicChatRateLimitBucket)
            .where(
                PublicChatRateLimitBucket.key_cache == kernel,
                PublicChatRateLimitBucket.window_start == window_ts,
                PublicChatRateLimitBucket.count < limit,
            )
            .values(
                count=PublicChatRateLimitBucket.count + 1,
                updated_at=func.now(),
            )
            .returning(PublicChatRateLimitBucket.count)
        )
        if result.mappings().first() is not None:
            return True

        result = await db.execute(
            pg_insert(PublicChatRateLimitBucket)
            .values(
                key_cache=kernel,
                window_start=window_ts,
                count=1,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    PublicChatRateLimitBucket.key_cache,
                    PublicChatRateLimitBucket.window_start,
                ]
            )
        )
        return bool(cast("CursorResult[Any]", result).rowcount)

    @classmethod
    async def prune(
        cls,
        db: AsyncSession,
        *,
        now_epoch: float | None = None,
    ) -> int:
        """Delete buckets older than the prune horizon; returns rows removed."""
        if now_epoch is None:
            now_epoch = time.time()
        cutoff = datetime.fromtimestamp(now_epoch - _PRUNE_WINDOW_SECONDS, tz=UTC)
        result = await db.execute(
            delete(PublicChatRateLimitBucket).where(
                PublicChatRateLimitBucket.window_start < cutoff
            )
        )
        return int(cast("CursorResult[Any]", result).rowcount or 0)


public_chat_rate_limiter_db = PublicChatRateLimiterDb()