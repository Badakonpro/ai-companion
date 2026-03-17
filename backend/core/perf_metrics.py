from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Generator


class PerfTracker:
    """Lightweight in-process performance tracker.

    Records timing samples per named operation.
    Keeps only the last N samples in a ring buffer for P50/P95/P99 computation.
    Also tracks error counts and token consumption (M4 5.2).
    """

    MAX_SAMPLES = 200

    def __init__(self) -> None:
        self._data: dict[str, deque[float]] = {}
        self._counts: dict[str, int] = {}
        self._errors: dict[str, int] = {}
        self._tokens: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self._lock = threading.Lock()

    def record(self, name: str, duration_ms: float) -> None:
        with self._lock:
            if name not in self._data:
                self._data[name] = deque(maxlen=self.MAX_SAMPLES)
                self._counts[name] = 0
            self._data[name].append(duration_ms)
            self._counts[name] += 1

    @contextmanager
    def measure(self, name: str) -> Generator[None, None, None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.record(name, elapsed_ms)

    def record_error(self, error_type: str) -> None:
        with self._lock:
            self._errors[error_type] = self._errors.get(error_type, 0) + 1

    def record_tokens(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        with self._lock:
            self._tokens["prompt"] += prompt_tokens
            self._tokens["completion"] += completion_tokens
            self._tokens["total"] += prompt_tokens + completion_tokens

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {}
            for name, samples in self._data.items():
                if not samples:
                    continue
                s = sorted(samples)
                n = len(s)
                result[name] = {
                    "count": self._counts.get(name, n),
                    "recent_samples": n,
                    "p50_ms": round(s[n // 2], 1),
                    "p95_ms": round(s[int(n * 0.95)], 1) if n >= 5 else round(s[-1], 1),
                    "p99_ms": round(s[int(n * 0.99)], 1) if n >= 10 else round(s[-1], 1),
                    "min_ms": round(s[0], 1),
                    "max_ms": round(s[-1], 1),
                    "mean_ms": round(statistics.mean(s), 1),
                }
            return result

    def error_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._errors)

    def token_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._tokens)

    def reset(self) -> None:
        with self._lock:
            self._data.clear()
            self._counts.clear()
            self._errors.clear()
            self._tokens = {"prompt": 0, "completion": 0, "total": 0}


# ── Global tracker ──────────────────────────────────────────────────
perf = PerfTracker()
