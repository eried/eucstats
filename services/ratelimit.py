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
_calls = 0


def _sweep(now: float, window_s: float) -> None:
    """Drop keys whose events have all expired — keeps the dict bounded to active keys."""
    cutoff = now - window_s
    for k in list(_hits.keys()):
        dq = _hits[k]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if not dq:
            del _hits[k]


def hit(key: str, limit: int, window_s: float = 3600.0) -> bool:
    """Record one event for `key`. Return True if it is within `limit` over the
    trailing `window_s`, False if the limit is already reached (caller should 429).
    A limit <= 0 disables the check (always allowed)."""
    if limit <= 0:
        return True
    now = time.monotonic()
    cutoff = now - window_s
    global _calls
    with _lock:
        _calls += 1
        if _calls % 500 == 0:           # periodic GC of quiet keys (bounds memory)
            _sweep(now, window_s)
        dq = _hits[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def retry_after(key: str, window_s: float = 3600.0) -> int:
    """Seconds until `key` has room again — 0 if it has room now.

    The limiter could say no and not say for how long, so every refusal reached a rider as a
    bare "slow down". The pairing limit is 30 opens per IP over a 3600-SECOND window, and the
    sign-in card told a locked-out stranger "Slow down a second." and offered them a retry
    button that walked straight into the same 429. Understating an hour as a second is worse
    than saying nothing, because it makes the only control on screen look like the answer.

    The window is a sliding one, so room appears when the OLDEST event in it expires, not when
    the whole window does.
    """
    now = time.monotonic()
    with _lock:
        dq = _hits.get(key)
        if not dq:
            return 0
        left = window_s - (now - dq[0])
    return max(0, int(left + 0.999))


def clear() -> None:
    """Wipe all counters (used by tests for isolation)."""
    with _lock:
        _hits.clear()
