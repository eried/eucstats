"""Lightweight ops log: one line per ingest + periodic health snapshots, appended
to DATA_DIR/health.log.

Like the audit log it's a flat, greppable file kept OUT of the dataset (survives
dataset switches), but it's higher-volume so it self-trims to a bounded size.
Best-effort: a logging failure never breaks ingestion.
"""
from __future__ import annotations

from datetime import datetime, timezone

import config

_TRIM_BYTES = 1_500_000     # ~1.5 MB -> trim
_KEEP_LINES = 4000          # keep the most recent N lines on trim


def _path():
    return config.DATA_DIR / "health.log"


def _append(line: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        p = _path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{ts}  {line}\n")
        if p.stat().st_size > _TRIM_BYTES:          # cheap size check, trim occasionally
            with open(p, encoding="utf-8") as f:
                lines = f.readlines()
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lines[-_KEEP_LINES:])
    except Exception:
        pass


def log_ingest(store, trip_uuid, status, *, dist=None, reasons=None,
               ms=None, size=None, dup=False) -> None:
    """One concise line per upload: outcome, who, timing, size, any flag reasons."""
    parts = [f"ingest  {status}{' dup' if dup else ''}",
             f"rider={(store or '?')[:8]}", f"trip={(trip_uuid or '?')[:8]}"]
    if dist is not None:
        parts.append(f"{dist}km")
    if size is not None:
        parts.append(f"{size}B")
    if ms is not None:
        parts.append(f"{round(ms)}ms")
    if reasons:
        parts.append("reasons=" + ",".join(reasons))
    _append("  ".join(parts))


def heartbeat(db) -> None:
    """Periodic health snapshot: public counts + server resource pressure."""
    try:
        from services import stats
        from services.sysinfo import system_stats
        s = stats.global_summary(db)
        syss = system_stats()
        mem, disk, cpu = syss.get("mem") or {}, syss.get("disk") or {}, syss.get("cpu") or {}
        load = (cpu.get("load") or [None])[0]
        _append(f"health  riders={s['riders']} trips={s['trips']} km={s['total_km']} "
                f"mem={mem.get('pct', '?')}% disk={disk.get('pct', '?')}% "
                f"load={load if load is not None else '?'}")
    except Exception:
        pass


def tail(n: int = 200) -> list[str]:
    """Most recent entries first."""
    try:
        with open(_path(), encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    return [ln.rstrip("\n") for ln in lines[-n:]][::-1]
