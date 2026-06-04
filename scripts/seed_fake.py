#!/usr/bin/env python
"""Seed the public eucstats server with demo riders + realistic trips.

Trips are built by RELOCATING + RETIMING the real sample CSVs in samples/ (which
carry the full schema — Battery level, Altitude, voltage/current, G-Force) to
each fake rider's country and date window. This keeps battery/ascent/efficiency
metrics working, unlike synthetic generation. Rider names/avatars come from
members.json (scraped EUC Planet group, gitignored). Public API only; idempotent.

Usage:
    python scripts/seed_fake.py
    EUCSTATS_URL=http://127.0.0.1:8004 python scripts/seed_fake.py
"""
import base64
import csv as csvmod
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
SAMPLES = os.path.join(ROOT, "samples")

COUNTRIES = [
    ("US", 39.8, -98.6), ("GB", 53.0, -1.5), ("DE", 51.0, 10.0), ("FR", 46.6, 2.4),
    ("NO", 60.4, 8.5), ("SE", 62.0, 15.0), ("NL", 52.1, 5.3), ("ES", 40.3, -3.7),
    ("IT", 42.8, 12.8), ("PL", 52.0, 19.4), ("CA", 51.0, -102.0), ("AU", -33.8, 151.0),
    ("JP", 35.7, 139.7), ("FI", 60.2, 24.9), ("DK", 55.7, 12.5), ("CH", 46.9, 8.3),
    ("AT", 48.2, 16.4), ("CZ", 50.1, 14.4), ("PT", 38.7, -9.1), ("SG", 1.35, 103.8),
    ("BR", -23.5, -46.6), ("MX", 19.4, -99.1),
]
WHEELS = [
    ("Begode", "Master"), ("Begode", "EX30"), ("Begode", "T4"), ("Begode", "Hero"),
    ("Veteran", "Sherman"), ("Veteran", "Sherman L"), ("Veteran", "Patton"), ("Veteran", "Lynx"),
    ("InMotion", "V13"), ("InMotion", "V12"), ("InMotion", "V11"), ("InMotion", "V14 Adventure"),
    ("KingSong", "S22"), ("KingSong", "S20"), ("KingSong", "S19"), ("KingSong", "16X"),
]
PHONES = [("Samsung", "Galaxy S24"), ("Samsung", "Galaxy S23"), ("Google", "Pixel 8"),
          ("Google", "Pixel 6"), ("Xiaomi", "13 Pro"), ("OnePlus", "12"), ("Samsung", "Galaxy A54"),
          ("Motorola", "Edge 40"), ("Xiaomi", "Redmi Note 12"), ("Sony", "Xperia 1 V"),
          ("Nothing", "Phone 2"), ("Asus", "ROG Phone 7")]
SDKS = [28, 29, 30, 30, 31, 31, 33, 33, 34, 34, 35, 36]
CCENTER = {c[0]: (c[1], c[2]) for c in COUNTRIES}
SPECIAL = {"tg_001": "IT"}   # Gio Aka Wheel In Motion is an Italian channel
EXCLUDE = {"EUCPlanet", "MotoEye / Smartglasses", "Erwin Ried", "Off-topic", "S T", "Adam"}
DATE_FMTS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
             "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S")


def clean_name(s):
    out = []
    for ch in s or "":
        o = ord(ch)
        if 0xE000 <= o <= 0xF8FF or 0xFE00 <= o <= 0xFE0F or o in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
            continue
        out.append(ch)
    return " ".join("".join(out).split())


def avatar_b64(m):
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


def parse_dt(s):
    s = (s or "").strip()
    for f in DATE_FMTS:
        try:
            return datetime.strptime(s, f), f
        except ValueError:
            continue
    return None, None


def load_template(path):
    rows = list(csvmod.reader(open(path, encoding="utf-8", errors="replace")))
    header, data = rows[0], [r for r in rows[1:] if r]
    low = [h.strip().lower() for h in header]
    di = low.index("date")
    la = low.index("latitude") if "latitude" in low else None
    lo = low.index("longitude") if "longitude" in low else None
    fmt = None
    for r in data:
        if di < len(r):
            _, fmt = parse_dt(r[di])
            if fmt:
                break
    olat = olon = None
    if la is not None and lo is not None:
        for r in data:
            try:
                olat, olon = float(r[la]), float(r[lo])
                break
            except (ValueError, IndexError):
                continue
    src = "darknessbot" if "pitch" in low else "eucplanet"
    sv = ("darknessbot-v1" if src == "darknessbot"
          else "eucplanet-v3-gforce" if "g-force" in low else "eucplanet-v1")
    return {"header": header, "data": data, "di": di, "la": la, "lo": lo,
            "fmt": fmt or DATE_FMTS[0], "olat": olat, "olon": olon, "src": src, "sv": sv}


def build_trip(t, hlat, hlon, start_dt, frac):
    """Relocate template GPS to (hlat,hlon) and retime so it starts at start_dt."""
    n = max(20, int(len(t["data"]) * frac))
    sub = t["data"][:n]
    first_dt, _ = parse_dt(sub[0][t["di"]]) if sub else (None, None)
    delta = (start_dt - first_dt) if first_dt else timedelta(0)
    olat, olon = t["olat"], t["olon"]
    cosr = (math.cos(math.radians(olat)) / max(0.2, math.cos(math.radians(hlat)))) if olat is not None else 1.0
    lines = [",".join(t["header"])]
    for row in sub:
        r = list(row)
        dt, _ = parse_dt(r[t["di"]]) if t["di"] < len(r) else (None, None)
        if dt:
            r[t["di"]] = (dt + delta).strftime(t["fmt"])
        if t["la"] is not None and olat is not None and t["la"] < len(r) and t["lo"] < len(r):
            try:
                r[t["la"]] = f"{hlat + (float(r[t['la']]) - olat):.6f}"
                r[t["lo"]] = f"{hlon + (float(r[t['lo']]) - olon) * cosr:.6f}"
            except ValueError:
                pass
        lines.append(",".join(r))
    return "\n".join(lines)


def main():
    templates = []
    for fn in sorted(os.listdir(SAMPLES)):
        if fn.lower().endswith(".csv"):
            try:
                templates.append(load_template(os.path.join(SAMPLES, fn)))
            except Exception as e:
                print("skip template", fn, e)
    print("templates:", [(t["src"], t["sv"], len(t["data"])) for t in templates])
    if not templates:
        print("no sample templates found"); return
    big = max(templates, key=lambda t: len(t["data"]))      # richest/longest
    today = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    total = 0
    with httpx.Client(base_url=BASE, timeout=120) as cl:
        for idx, m in enumerate(MEMBERS):
            rnd = random.Random(1000 + idx)
            name = clean_name(m.get("name") or f"Rider {idx}")[:40] or f"Rider {idx}"
            if name in EXCLUDE:
                continue
            code, clat, clon = COUNTRIES[idx % len(COUNTRIES)]
            brand, model = WHEELS[rnd.randrange(len(WHEELS))]
            sid = f"tg_{idx:03d}"
            if sid in SPECIAL:
                code = SPECIAL[sid]
                clat, clon = CCENTER[code]
            hlat = clat + rnd.uniform(-0.4, 0.4)
            hlon = clon + rnd.uniform(-0.6, 0.6)
            payload = {"store_id": sid, "display_name": name, "flag": code, "consent_public": True}
            ab = avatar_b64(m)
            if ab:
                payload["avatar_png_base64"] = ab
            r = cl.post("/api/v1/riders", json=payload)

            serial = f"{brand[:2].upper()}{idx:04d}{rnd.randrange(1000, 9999)}"
            pb, pm = PHONES[rnd.randrange(len(PHONES))]
            sdk = rnd.choice(SDKS)
            build = rnd.randint(410, 466)
            appver = f"3.{(build - 400) // 10}.{(build - 400) % 10}"
            streak = rnd.randint(1, 9 if idx < 6 else 5)
            extra = rnd.randint(0, 4 if idx < 10 else 2)
            base_off = rnd.randint(0, 40)
            days = {base_off + d for d in range(streak)}
            for _ in range(extra):
                days.add(rnd.randint(0, 70))

            trips = 0
            for j, doff in enumerate(sorted(days)):
                t = rnd.choices(templates + [big, big], k=1)[0]   # weight the rich/long one
                frac = rnd.uniform(0.25, 0.85) if len(t["data"]) > 400 else 1.0
                day = today - timedelta(days=doff)
                start = day.replace(hour=rnd.randint(7, 19), minute=rnd.randint(0, 59))
                csvtxt = build_trip(t, hlat, hlon, start, frac)
                meta = {
                    "store_id": sid, "platform": "google_play",
                    "trip_uuid": str(uuid.uuid5(NS, f"{sid}|{doff}|{j}")),
                    "source_app": t["src"], "schema_version": t["sv"],
                    "tz": "UTC", "tz_offset_min": 0, "tz_known": True, "is_mock_location": False,
                    "wheel": {"serial": serial, "brand": brand, "model": model},
                    "app_version": appver, "app_build": build, "os_version": f"Android {sdk - 20}",
                    "device": {"manufacturer": pb, "model": pm, "sdk_int": sdk,
                               "screen_resolution": "1080x2400", "locale": "en-US"},
                    "sample_interval_ms": 1000,
                    "attestation": {"type": "play_integrity", "token": "stub", "request_hash": "x"},
                }
                files = {"trip": (f"{serial}_{doff}.csv.gz", gzip.compress(csvtxt.encode()), "application/gzip")}
                rr = cl.post("/api/v1/trips", data={"meta": json.dumps(meta)}, files=files)
                if rr.status_code in (200, 201):
                    trips += 1
                else:
                    print(f"  trip fail {sid} {rr.status_code} {rr.text[:120]}")
            total += trips
            print(f"[{idx + 1:2d}/{len(MEMBERS)}] {sid} {name[:20]:20s} {code} {brand} {model} "
                  f"reg={r.status_code} trips={trips}", flush=True)
    print("TOTAL trips uploaded:", total)


if __name__ == "__main__":
    main()
