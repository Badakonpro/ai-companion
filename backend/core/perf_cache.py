from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    """Thread-safe, per-key TTL LRU cache (L1 in-process cache).

    Designed for hot session data: state, facts, emotion, episodes.
    Eviction: LRU when exceeding max_size, or TTL expiry.
    """

    def __init__(self, max_size: int = 256, default_ttl: float = 30.0) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()  # key → (value, expire_at)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, expire_at = entry
            if time.monotonic() > expire_at:
                del self._store[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        expire_at = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expire_at)
            # Evict oldest if over limit
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Invalidate all keys starting with prefix."""
        with self._lock:
            keys_to_del = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_del:
                del self._store[k]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
            }


# ── Global L1 cache instance ───────────────────────────────────────
# Shared across all modules; keyed by "{domain}:{session_id}"
l1_cache = TTLCache(max_size=512, default_ttl=30.0)


def cache_key(domain: str, session_id: str) -> str:
    return f"{domain}:{session_id}"
