"""Lightweight server resource stats (disk / memory / CPU) using only stdlib.

Linux-first (reads /proc, os.getloadavg); degrades gracefully elsewhere so it
never breaks local dev or tests."""
from __future__ import annotations

import os
import shutil

import config


def _disk(path: str) -> dict | None:
    try:
        du = shutil.disk_usage(path)
        return {"total": du.total, "used": du.used, "free": du.free,
                "pct": round(du.used / du.total * 100, 1) if du.total else 0.0}
    except Exception:
        return None


def _mem() -> dict | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.split()[0]) * 1024   # kB -> bytes
        total = info.get("MemTotal")
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if not total:
            return None
        used = total - avail
        return {"total": total, "avail": avail, "used": used,
                "pct": round(used / total * 100, 1)}
    except Exception:
        return None


def _cpu() -> dict:
    count = os.cpu_count() or 1
    try:
        load = [round(x, 2) for x in os.getloadavg()]     # (1, 5, 15) min
    except (OSError, AttributeError):
        load = None
    pct = round(min(100.0, (load[0] / count) * 100), 1) if load else None
    return {"count": count, "load": load, "pct": pct}


def system_stats(path: str | None = None) -> dict:
    path = path or str(config.DATA_DIR)
    return {"disk": _disk(path), "mem": _mem(), "cpu": _cpu()}
