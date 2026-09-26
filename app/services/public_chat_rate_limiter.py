"""In-memory sliding-window rate limits for the public chat API.

Deliberately process-local (single-instance deployment is the 1P.1 topology);
DB-backed counters are the documented Phase 1P.2 upgrade path. False negatives
(over-limiting under concurrency) are safe; the limiter is a burst guard, not
an admission authority — the durable abuse guards are the per-session idempotent
message write, the per-config max-active-sessions count, and input bounds.
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


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
    """Sliding-window per-(scope, key) limiter with periodic cleanup."""

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


public_chat_rate_limiter = PublicChatRateLimiter()