"""Dead-simple admin audit trail: append-only lines in a flat file (DATA_DIR/audit.log).

Deliberately NOT in the database/dataset — it must survive dataset switches and stay
trivially greppable. The admin page just tails this file. Best-effort: a logging
failure never breaks the action it records.
"""
from __future__ import annotations

from datetime import datetime, timezone

import config


def _path():
    return config.DATA_DIR / "audit.log"


def log(action: str, detail: str = "") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {action:<16}  {detail}".rstrip()
    try:
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tail(n: int = 300) -> list[str]:
    """Most recent entries first."""
    try:
        with open(_path(), encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return [ln.rstrip("\n") for ln in lines[-n:]][::-1]
