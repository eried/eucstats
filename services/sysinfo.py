"""Lightweight server resource stats (disk / memory / CPU) using only stdlib.

Linux-first (reads /proc, os.getloadavg); degrades gracefully elsewhere so it
never breaks local dev or tests."""
from __future__ import annotations

import os
import shutil
import time

import config

_FOOTPRINT_CACHE = {"t": 0.0, "val": None}


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


def _dir_size(path: str) -> int:
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += _dir_size(entry.path)
                except OSError:
                    continue
    except OSError:
        pass
    return total


def app_footprint(path: str) -> dict | None:
    """Our whole install's disk usage + a per-child (file/folder) breakdown."""
    try:
        items = []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        items.append((entry.name + "/", _dir_size(entry.path)))
                    elif entry.is_file(follow_symlinks=False):
                        items.append((entry.name, entry.stat(follow_symlinks=False).st_size))
                except OSError:
                    continue
        items.sort(key=lambda kv: -kv[1])
        return {"path": path, "bytes": sum(b for _, b in items), "breakdown": items}
    except OSError:
        return None


def _cpu() -> dict:
    count = os.cpu_count() or 1
    try:
        load = [round(x, 2) for x in os.getloadavg()]     # (1, 5, 15) min
    except (OSError, AttributeError):
        load = None
    pct = round(min(100.0, (load[0] / count) * 100), 1) if load else None
    return {"count": count, "load": load, "pct": pct}


def app_footprint_cached(path: str, ttl: float = 60.0) -> dict | None:
    """Footprint walk is expensive (whole install); cache it so the System page's
    autorefresh can poll the cheap disk/mem/cpu stats every few seconds."""
    now = time.time()
    if _FOOTPRINT_CACHE["val"] is None or (now - _FOOTPRINT_CACHE["t"]) > ttl:
        _FOOTPRINT_CACHE["val"] = app_footprint(path)
        _FOOTPRINT_CACHE["t"] = now
    return _FOOTPRINT_CACHE["val"]


def system_stats(path: str | None = None) -> dict:
    path = path or str(config.DATA_DIR)
    return {"disk": _disk(path), "mem": _mem(), "cpu": _cpu(),
            "app": app_footprint_cached(str(config.BASE_DIR))}
