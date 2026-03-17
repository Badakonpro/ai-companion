"""In-memory sliding-window rate limiter (M4 5.3).

Uses a per-key deque of timestamps. Thread-safe via a lock.
Designed for single-process local mode; for server mode a Redis-based
implementation would replace this.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_requests: int = 20, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = deque()
            bucket = self._buckets[key]
            # Purge expired timestamps
            cutoff = now - self.window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return self.max_requests
            cutoff = now - self.window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return max(0, self.max_requests - len(bucket))
