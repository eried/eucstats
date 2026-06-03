#!/usr/bin/env python
"""Seed the public eucstats server with demo riders + plausible trips.

Rider names/avatars come from members.json (scraped EUC Planet group, gitignored).
Riders are spread across many countries / wheel brands / models so the country,
brand and wheel leaderboards and the world heatmap all populate. Hits the public
API only. Idempotent: re-running re-registers riders and skips duplicate trips.

Usage:
    python scripts/seed_fake.py
    EUCSTATS_URL=http://127.0.0.1:8004 python scripts/seed_fake.py
"""
import base64
import gzip
import io
import json
import math
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("EUCSTATS_URL", "https://eucstats.ried.no")
NS = uuid.UUID("00000000-0000-0000-0000-0000000000fa")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMBERS = json.load(open(os.path.join(ROOT, "members.json"), encoding="utf-8"))

# (flag, center lat, center lon) — EUC-popular countries, spread globally
COUNTRIES = [
    ("US", 39.8, -98.6), ("GB", 53.0, -1.5), ("DE", 51.0, 10.0), ("FR", 46.6, 2.4),
    ("NO", 60.4, 8.5), ("SE", 62.0, 15.0), ("NL", 52.1, 5.3), ("ES", 40.3, -3.7),
    ("IT", 42.8, 12.8), ("PL", 52.0, 19.4), ("CA", 51.0, -102.0), ("AU", -33.8, 151.0),
    ("JP", 35.7, 139.7), ("FI", 60.2, 24.9), ("DK", 55.7, 12.5), ("CH", 46.9, 8.3),
    ("AT", 48.2, 16.4), ("CZ", 50.1, 14.4), ("PT", 38.7, -9.1), ("SG", 1.35, 103.8),
    ("BR", -23.5, -46.6), ("MX", 19.4, -99.1),
]

# (brand, model, full-charge pack voltage, realistic top cruise km/h)
WHEELS = [
    ("Begode", "Master", 134.4, 70), ("Begode", "EX30", 151.2, 78),
    ("Begode", "T4", 100.8, 55), ("Begode", "Hero", 134.4, 65),
    ("Veteran", "Sherman", 100.8, 60), ("Veteran", "Sherman L", 151.2, 75),
    ("Veteran", "Patton", 126.0, 70), ("Veteran", "Lynx", 151.2, 72),
    ("InMotion", "V13", 126.0, 70), ("InMotion", "V12", 100.8, 55),
    ("InMotion", "V11", 84.0, 50), ("InMotion", "V14 Adventure", 151.2, 80),
    ("KingSong", "S22", 126.0, 65), ("KingSong", "S20", 100.8, 55),
    ("KingSong", "S19", 126.0, 68), ("KingSong", "16X", 84.0, 45),
]


def avatar_b64(m):
    """Decode the scraped avatar and re-encode to a 64x64 PNG (server re-encodes
    again; doing it here avoids depending on server-side webp support)."""
    a = m.get("avatar")
    if not a:
        return None
    try:
        if a.startswith("data:"):
            a = a.split(",", 1)[1]
        im = Image.open(io.BytesIO(base64.b64decode(a))).convert("RGBA").resize((64, 64))
        out = io.BytesIO()
        im.save(out, format="PNG")
        return base64.b64encode(out.getvalue()).decode()
    except Exception:
        return None


def build_trip(rnd, lat0, lon0, pack, vmax, start):
    """Build a plausible per-second-ish trip CSV (GPS + speed + electrical + g)."""
    cad = 3                                   # seconds between samples
    n = max(40, rnd.randint(6, 22) * 60 // cad)
    cruise = rnd.uniform(max(20.0, vmax * 0.55), vmax * 0.96)
    ramp = rnd.randint(2, 4)                  # steps to reach cruise (~6-12s)
    stat = 2                                  # standing-still samples (enables 0->40)
    heading = rnd.uniform(0, 2 * math.pi)
    lat, lon, prev = lat0, lon0, 0.0
    rows = ["Date,Speed,Voltage,Current,Power,Latitude,Longitude,G-Force"]
    t = start
    for k in range(n):
        if k < stat:
            sp = 0.0
        elif k < stat + ramp:
            sp = cruise * (k - stat + 1) / ramp
        elif k > n - 4:
            sp = max(0.0, cruise * (n - k) / 4)
        else:
            sp = cruise + rnd.uniform(-3, 3)
        sp = max(0.0, min(vmax + 2, sp))
        acc = sp - prev
        heading += rnd.uniform(-0.25, 0.25)
        stepkm = sp * (cad / 3600.0)
        lat += stepkm / 111.0 * math.cos(heading)
        lon += stepkm / (111.0 * max(0.2, math.cos(math.radians(lat)))) * math.sin(heading)
        load = sp / max(1.0, cruise)
        volt = pack * 0.985 - pack * 0.12 * load + rnd.uniform(-0.4, 0.4)
        cur = min(95.0, max(0.0, 4.0 + sp * 0.55 + max(0.0, acc) * 3.0 + rnd.uniform(-2, 3)))
        power = volt * cur
        g = min(2.4, 1.0 + abs(acc) * 0.06 + rnd.uniform(-0.03, 0.05))
        rows.append(f"{t.strftime('%Y-%m-%dT%H:%M:%S.%f')},{sp:.2f},{volt:.1f},"
                    f"{cur:.1f},{power:.0f},{lat:.6f},{lon:.6f},{g:.3f}")
        prev = sp
        t += timedelta(seconds=cad)
    return "\n".join(rows)


def main():
    today = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    total = 0
    with httpx.Client(base_url=BASE, timeout=120) as cl:
        for idx, m in enumerate(MEMBERS):
            rnd = random.Random(1000 + idx)
            name = (m.get("name") or f"Rider {idx}").strip()[:40]
            code, clat, clon = COUNTRIES[idx % len(COUNTRIES)]
            brand, model, pack, vmax = WHEELS[rnd.randrange(len(WHEELS))]
            sid = f"tg_{idx:03d}"
            hlat = clat + rnd.uniform(-0.4, 0.4)
            hlon = clon + rnd.uniform(-0.6, 0.6)
            payload = {"store_id": sid, "display_name": name, "flag": code, "consent_public": True}
            ab = avatar_b64(m)
            if ab:
                payload["avatar_png_base64"] = ab
            r = cl.post("/api/v1/riders", json=payload)

            serial = f"{brand[:2].upper()}{idx:04d}{rnd.randrange(1000, 9999)}"
            streak = rnd.randint(1, 11 if idx < 6 else 6)
            extra = rnd.randint(0, 5 if idx < 10 else 3)
            base_off = rnd.randint(0, 40)
            days = {base_off + d for d in range(streak)}
            for _ in range(extra):
                days.add(rnd.randint(0, 70))

            trips = 0
            for j, doff in enumerate(sorted(days)):
                day = today - timedelta(days=doff)
                start = day.replace(hour=rnd.randint(7, 19), minute=rnd.randint(0, 59))
                csv = build_trip(rnd, hlat, hlon, pack, vmax, start)
                meta = {
                    "store_id": sid, "platform": "google_play",
                    "trip_uuid": str(uuid.uuid5(NS, f"{sid}|{doff}|{j}")),
                    "source_app": "eucplanet", "schema_version": "auto",
                    "tz": "UTC", "tz_offset_min": 0, "tz_known": True,
                    "is_mock_location": False,
                    "wheel": {"serial": serial, "brand": brand, "model": model},
                    "attestation": {"type": "play_integrity", "token": "stub", "request_hash": "x"},
                }
                files = {"trip": (f"{serial}_{doff}.csv.gz", gzip.compress(csv.encode()), "application/gzip")}
                rr = cl.post("/api/v1/trips", data={"meta": json.dumps(meta)}, files=files)
                if rr.status_code in (200, 201):
                    trips += 1
                else:
                    print(f"  trip fail {sid} {rr.status_code} {rr.text[:120]}")
            total += trips
            print(f"[{idx + 1:2d}/{len(MEMBERS)}] {sid} {name[:22]:22s} {code} "
                  f"{brand} {model} reg={r.status_code} trips={trips}", flush=True)
    print("TOTAL trips uploaded:", total)


if __name__ == "__main__":
    main()
