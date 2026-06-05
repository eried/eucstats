"""In-memory sliding-window rate limiter.

Keys (IP, store_id) live only in memory and are never persisted — privacy-friendly
and reset on restart, which is fine for flood protection. The app runs a single
worker, so one process-wide store covers all requests; a lock keeps it safe anyway.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def hit(key: str, limit: int, window_s: float = 3600.0) -> bool:
    """Record one event for `key`. Return True if it is within `limit` over the
    trailing `window_s`, False if the limit is already reached (caller should 429).
    A limit <= 0 disables the check (always allowed)."""
    if limit <= 0:
        return True
    now = time.monotonic()
    cutoff = now - window_s
    with _lock:
        dq = _hits[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        if not dq:                      # keep the dict from growing unbounded
            _hits.pop(key, None)
        return True


def clear() -> None:
    """Wipe all counters (used by tests for isolation)."""
    with _lock:
        _hits.clear()
