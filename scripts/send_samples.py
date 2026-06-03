#!/usr/bin/env python
"""E2E driver: register demo riders and upload local sample trip CSVs to a
running eucstats server. Hits the public API only (no server credentials).

Usage:
    python scripts/send_samples.py                 # -> https://eucstats.ried.no
    EUCSTATS_URL=http://127.0.0.1:8004 python scripts/send_samples.py
"""
import glob
import gzip
import json
import os
import uuid

import httpx

BASE = os.environ.get("EUCSTATS_URL", "https://eucstats.ried.no")
NS = uuid.UUID("00000000-0000-0000-0000-0000000000ec")

RIDERS = [
    ("u_demo_1", "Erwin", "NO"),
    ("u_demo_2", "Tester2", "SE"),
]


def _store_for(fn: str) -> str:
    return "u_demo_2" if fn.startswith("F4E02AB02A75") else "u_demo_1"


def _serial(fn: str):
    prefix = fn.split("_")[0]
    return prefix if len(prefix) == 12 and all(c in "0123456789ABCDEFabcdef" for c in prefix) else None


def _meta(fn: str, store: str, header: str) -> dict:
    is_db = "pitch" in header.lower()           # DarknessBot columns
    serial = _serial(fn)
    return {
        "store_id": store, "platform": "google_play",
        "trip_uuid": str(uuid.uuid5(NS, f"{store}|{fn}")),
        "source_app": "darknessbot" if is_db else "eucplanet",
        "schema_version": "auto",
        "tz": "UTC" if is_db else "Europe/Oslo",
        "tz_offset_min": 0 if is_db else 120,
        "tz_known": not is_db,
        "is_mock_location": False,
        "wheel": {"serial": serial, "model": "unknown"} if serial else {},
        "attestation": {"type": "play_integrity", "token": "stub", "request_hash": "x"},
    }


def main():
    with httpx.Client(base_url=BASE, timeout=60) as cl:
        for sid, name, flag in RIDERS:
            r = cl.post("/api/v1/riders", json={"store_id": sid, "display_name": name, "flag": flag})
            print(f"register {sid:10s} -> {r.status_code}")
        for path in sorted(glob.glob("samples/*.csv")):
            fn = os.path.basename(path)
            store = _store_for(fn)
            text = open(path, encoding="utf-8", errors="replace").read()
            meta = _meta(fn, store, text.splitlines()[0])
            files = {"trip": (fn + ".gz", gzip.compress(text.encode()), "application/gzip")}
            r = cl.post("/api/v1/trips", data={"meta": json.dumps(meta)}, files=files)
            print(f"upload {fn:34s} -> {store} {r.status_code} {r.text[:180]}")


if __name__ == "__main__":
    main()
