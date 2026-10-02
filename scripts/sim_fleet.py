"""A fake fleet: riders, rides and uploads, driven through the real API.

Why this exists instead of a rack of emulators
----------------------------------------------
Crews is mostly server and web. The phone's only job in it is to scan a QR code and say
"yes, that is me" — one HTTP round trip. Everything else (crew creation, joining, approvals,
territory, the map) is a browser talking to the server. So a fleet of emulators would be a lot
of machinery to test the one part that needs the least of it.

What DOES need to be real is the ingest path, because territory is derived from GPS tracks and
nothing else. So this script does not write rows into the database. It registers riders through
`POST /api/v1/riders` and uploads app-format CSVs through `POST /api/v1/trips`, exactly as the
phone does, and lets the real parser, summariser, validator and aggregator do their work. If a
simulated ride would be flagged as a teleport or an odo mismatch in production, it is flagged
here too — which has already caught bugs in the generator, and is the point.

Two things are physical rather than decorative:

* **Position comes from the speed.** The odometer column, the GPS distance and the speed column
  cannot disagree, because there is one number underneath all three. That is exactly what the
  odo/GPS mismatch rule checks.
* **Power comes from a force balance** — rolling resistance, aerodynamic drag and the work of
  accelerating a rider-plus-wheel mass — and the voltage sags against the current through a
  pack resistance. Earlier drafts used an arbitrary multiple of speed and produced 6.7 kW at
  7 km/h, which would have gone straight onto the power and current leaderboards. Simulated
  data that lands on a leaderboard has to be as believable as real data.

Usage
-----
    python scripts/sim_fleet.py --base http://127.0.0.1:8000 --riders 12 --days 90
    python scripts/sim_fleet.py --list-cities
    python scripts/sim_fleet.py --dry-run --riders 2       # generate, print, upload nothing

Deterministic: the same --seed gives the same fleet, so screenshots are reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:                                   # --dry-run does not need it
    requests = None

# (name, lat, lon, flag) — real places, so reverse geocoding gives real countries and the map
# looks like a map. Spread deliberately across latitudes: territory tiles shrink toward the
# poles, and a bug that only shows up near the equator should have somewhere to show up.
CITIES = [
    ("Oslo", 59.9139, 10.7522, "NO"),
    ("Tromso", 69.6492, 18.9553, "NO"),
    ("Copenhagen", 55.6761, 12.5683, "DK"),
    ("Berlin", 52.5200, 13.4050, "DE"),
    ("Paris", 48.8566, 2.3522, "FR"),
    ("Barcelona", 41.3851, 2.1734, "ES"),
    ("New York", 40.7128, -74.0060, "US"),
    ("Austin", 30.2672, -97.7431, "US"),
    ("Singapore", 1.3521, 103.8198, "SG"),
    ("Santiago", -33.4489, -70.6693, "CL"),
    ("Sydney", -33.8688, 151.2093, "AU"),
]

WHEELS = [
    ("Begode", "Master", "GW2026202"),
    ("Begode", "Panther", "GW2026201"),
    ("LeaperKim", "Sherman L", "LK19486"),
    ("LeaperKim", "Patton", "LK20110"),
    ("Veteran", "Lynx", "VT1042"),
    ("InMotion", "V13", "IM3301"),
    ("KingSong", "S22 Pro", "KS1817"),
]

NAMES = ["Nordlys", "Tarmac", "Fjord Runner", "Night Shift", "Volt", "Kerb Hopper",
         "Sidewinder", "Pavement", "Torque", "Dyno", "Switchback", "Cadence", "Ember",
         "Gravel", "Hushpuppy", "Longhaul", "Midnight Oil", "Pylon", "Quartz", "Ratchet"]

EARTH = 111.32          # km per degree of latitude

# --- the machine being simulated ---------------------------------------------------------
MASS_KG = 112.0         # rider plus wheel
CRR = 0.012             # rolling resistance on tarmac
CDA = 0.52              # drag area of a person standing on a wheel, m^2
RHO = 1.225             # air density, kg/m^3
ETA = 0.84              # drivetrain + inverter efficiency
PACK_V = 100.8          # 24s nominal, full
PACK_EMPTY_V = 79.0
PACK_R = 0.075          # internal resistance, ohms -> the voltage sag under load
ACCEL_KMHS = 7.0        # how briskly it gains speed, km/h per second (0.20 g)
BRAKE_KMHS = 9.0        # and loses it (0.25 g) — both well inside the validator's limits


def _km_to_deg(dx_km: float, dy_km: float, lat: float) -> tuple[float, float]:
    return dy_km / EARTH, dx_km / (EARTH * max(0.05, math.cos(math.radians(lat))))


def loop_route(rng: random.Random, lat0: float, lon0: float, km: float,
               n: int = 600) -> list[tuple[float, float]]:
    """A closed, wobbly loop of roughly `km` through the point given.

    A loop rather than a line because that is what riding looks like — you come home — and
    because a loop encloses tiles, which is what makes territory interesting to look at. The
    shape is a few harmonics in polar coordinates, so no two rides trace the same path and
    none of them is a circle.
    """
    harmonics = [(rng.uniform(0.08, 0.3), rng.randint(2, 5), rng.uniform(0, 6.283))
                 for _ in range(3)]
    raw = []
    for i in range(n + 1):
        th = 2 * math.pi * i / n
        r = 1.0 + sum(a * math.cos(k * th + p) for a, k, p in harmonics)
        raw.append((r * math.cos(th), r * math.sin(th)))
    peri = sum(math.dist(raw[i], raw[i + 1]) for i in range(n))
    scale = km / peri if peri else 1.0
    out = []
    for x, y in raw:
        dlat, dlon = _km_to_deg(x * scale, y * scale, lat0)
        out.append((lat0 + dlat, lon0 + dlon))
    return out


def segment_km(route: list[tuple[float, float]]) -> list[float]:
    out = []
    for i in range(len(route) - 1):
        (la1, lo1), (la2, lo2) = route[i], route[i + 1]
        mlat = math.radians((la1 + la2) / 2)
        out.append(math.hypot((lo2 - lo1) * EARTH * math.cos(mlat), (la2 - la1) * EARTH))
    return out


def speed_profile(rng: random.Random, km: float, cruise: float) -> list[float]:
    """One speed per second for a ride of `km`, starting and ending stopped.

    Distance-driven rather than duration-driven. An earlier version guessed the duration from
    an assumed average speed, guessed low, and so every ride was truncated the moment the loop
    ran out — ending at 29 km/h with no deceleration, which is not a ride, it is a crash with
    no crash. Here the rider brakes when the remaining distance is exactly the braking
    distance, so the ride ends at a standstill at the right place every time.
    """
    stops = sorted(rng.uniform(0.12, 0.88) * km
                   for _ in range(min(5, max(1, int(km / 5)))))
    sprint_at = rng.uniform(0.3, 0.8) * km
    out: list[float] = []
    v = dist = 0.0
    stop_hold = 0
    si = 0
    guard = int(km / 2.0 * 3600) + 600                 # a 2 km/h floor on the average
    # the ride is over when the last few metres are gone, NOT when dist == km exactly: once
    # the rider has braked to a standstill, a loop conditioned on distance alone never
    # advances again, and an earlier draft spent nine simulated hours parked at the finish
    while km - dist > 0.003 and len(out) < guard:
        remaining = km - dist
        brake_km = v * v / 64800.0 * (BRAKE_KMHS / 9.0)     # distance needed to reach 0
        if remaining <= brake_km + 0.004:
            target = 0.0
            if v < 0.2:                                # arrived: stop generating
                break
        elif stop_hold > 0:
            target, stop_hold = 0.0, stop_hold - 1
        elif si < len(stops) and dist >= stops[si]:
            si += 1
            stop_hold = rng.randrange(6, 26)           # a junction, a light, a photo
            target = 0.0
        elif sprint_at <= dist < sprint_at + 0.25:
            target = cruise * 1.42                     # the one bit of showing off per ride
        else:
            target = cruise + rng.uniform(-3.5, 3.5)
        rate = ACCEL_KMHS if target > v else BRAKE_KMHS
        v = max(0.0, v + max(-rate, min(rate, target - v)))
        out.append(round(v, 1))
        dist += v / 3600.0
    out.extend([0.0, 0.0])                             # stood still a moment before shutdown
    return out


def _walk(route: list[tuple[float, float]], speeds: list[float]):
    """Advance along `route` at the given speeds, yielding (lat, lon, v, odo) per second."""
    seg = segment_km(route)
    i, along, odo = 0, 0.0, 0.0
    for v in speeds:
        odo += v / 3600.0
        remaining = v / 3600.0
        while remaining > 1e-12 and i < len(seg):
            left = seg[i] - along
            if remaining < left:
                along += remaining
                remaining = 0.0
            else:
                remaining -= left
                i += 1
                along = 0.0
        if i >= len(seg):
            yield route[-1][0], route[-1][1], v, odo
            return
        f = along / seg[i] if seg[i] else 0.0
        (la1, lo1), (la2, lo2) = route[i], route[i + 1]
        yield la1 + (la2 - la1) * f, lo1 + (lo2 - lo1) * f, v, odo


def _power_w(v_kmh: float, prev_kmh: float, grade: float) -> float:
    """Shaft power from a force balance. Negative while braking — a wheel regenerates."""
    v = v_kmh / 3.6
    a = (v_kmh - prev_kmh) / 3.6                       # m/s^2 over a 1 s step
    f = MASS_KG * 9.81 * CRR + 0.5 * RHO * CDA * v * v + MASS_KG * 9.81 * grade + MASS_KG * a
    p = f * v
    return p / ETA if p > 0 else p * ETA               # regen comes back lossily too


HEADER = ("Date,Speed,GPS Speed,Voltage,PWM,Current,Power,Battery level,Total mileage,"
          "Temperature,Latitude,Longitude,Altitude")


def ride_csv(rng: random.Random, lat0: float, lon0: float, start: datetime,
             km: float, cruise: float) -> tuple[str, dict]:
    """One ride as an app-format CSV, plus the facts the uploader needs about it."""
    route = loop_route(rng, lat0, lon0, km)
    speeds = speed_profile(rng, km, cruise)
    base_alt = rng.uniform(5, 220)
    hill = rng.uniform(10, 85)
    # a whole number of hill laps, so a closed route comes back to the altitude it left from.
    # With a fractional count the ride ended 70 m below its own start and the ascent and
    # descent totals disagreed for no reason a rider could have caused.
    laps = rng.choice([1, 1, 2])
    drain = rng.uniform(55, 80)                        # % of pack spent over the whole ride
    buf = io.StringIO()
    buf.write(HEADER + "\n")
    rows, last_odo, peak_w, temp = 0, 0.0, 0.0, 24.0 + rng.uniform(-3, 6)
    prev_v, prev_alt = 0.0, None
    for n, (lat, lon, v, odo) in enumerate(_walk(route, speeds)):
        frac = min(1.0, odo / max(km, 0.01))
        alt = base_alt + hill * math.sin(frac * 2 * math.pi * laps)
        grade = 0.0 if prev_alt is None or v < 0.5 else \
            max(-0.14, min(0.14, (alt - prev_alt) / max(1.0, v / 3.6)))
        watts = _power_w(v, prev_v, grade)
        ocv = PACK_V - (PACK_V - PACK_EMPTY_V) * frac * rng.uniform(0.93, 1.0)
        amps = watts / max(60.0, ocv)
        volts = ocv - amps * PACK_R                    # sags under load, lifts under regen
        # the board heats with the work it is doing and sheds it with airspeed
        temp += (abs(watts) / 9000.0) - (temp - 22.0) * (0.0008 + v / 26000.0)
        temp = max(18.0, min(78.0, temp))
        # GPS speed tracks the wheel but lags it, as a receiver does. The accel metrics need
        # both columns present to corroborate each other.
        gps_v = max(0.0, prev_v * 0.55 + v * 0.45 + rng.uniform(-1, 1))
        t = start + timedelta(seconds=n)
        buf.write("%s,%.1f,%.1f,%.2f,%.0f,%.1f,%.0f,%.0f,%.3f,%.1f,%.7f,%.7f,%.1f\n" % (
            t.strftime("%Y-%m-%dT%H:%M:%S.%f"), v, gps_v, volts,
            max(0.0, min(99.0, v * 1.15 + max(0.0, amps) * 0.42)), amps, watts,
            max(2.0, 100 - frac * drain),              # monotonic: a pack does not refill
            odo, temp, lat, lon, alt + rng.uniform(-0.5, 0.5)))
        rows, last_odo, prev_v, prev_alt = rows + 1, odo, v, alt
        peak_w = max(peak_w, watts)
    return buf.getvalue(), {"rows": rows, "km": last_odo, "peak_w": peak_w,
                            "end": start + timedelta(seconds=rows)}


def register(base: str, store_id: str, name: str, flag: str) -> None:
    r = requests.post(f"{base}/api/v1/riders",
                      json={"store_id": store_id, "display_name": name, "flag": flag},
                      timeout=30)
    if r.status_code >= 400:
        raise SystemExit(f"register {name} failed: {r.status_code} {r.text[:200]}")


def upload(base: str, store_id: str, csv_text: str, start: datetime, end: datetime,
           wheel: tuple[str, str, str], tz_off: int) -> dict:
    data = csv_text.encode()
    mac = hashlib.sha1(f"{store_id}{wheel[1]}".encode()).hexdigest()[:12].upper()
    meta = {
        "store_id": store_id, "trip_uuid": str(uuid.uuid4()),
        "start_utc": start.replace(tzinfo=timezone.utc).isoformat(),
        "end_utc": end.replace(tzinfo=timezone.utc).isoformat(),
        "tz_offset_min": tz_off, "tz": f"UTC{tz_off // 60:+d}", "tz_known": True,
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "schema_version": "2", "source_app": "eucplanet", "app_version": "sim",
        "platform": "google_play", "is_mock_location": False,
        "wheel": {"brand": wheel[0], "model": wheel[1], "firmware": wheel[2], "ble_mac": mac},
    }
    r = requests.post(f"{base}/api/v1/trips",
                      data={"meta": json.dumps(meta)},
                      files={"trip": ("trip.csv", data, "text/csv")}, timeout=120)
    if r.status_code >= 400:
        return {"error": f"{r.status_code} {r.text[:160]}"}
    return r.json()


def fleet(seed: int, riders: int, city: str = "") -> list[dict]:
    """The fleet as data, so other scripts can address a simulated rider by name.

    Scenario tests need to say "the Oslo rider with the most kilometres" and get a store_id,
    without re-deriving the generator's own random choices.
    """
    rng = random.Random(seed)
    pool = [c for c in CITIES if not city or c[0].lower() == city.lower()]
    out = []
    for i in range(riders):
        c = pool[i % len(pool)] if pool else CITIES[0]
        wheel = WHEELS[rng.randrange(len(WHEELS))]
        keen = rng.choice([2, 4, 7, 12, 20])
        cruise = rng.uniform(22, 38)
        hlat = c[1] + rng.uniform(-0.05, 0.05)
        hlon = c[2] + rng.uniform(-0.05, 0.05)
        name = NAMES[i % len(NAMES)] + (f" {i // len(NAMES) + 1}" if i >= len(NAMES) else "")
        out.append({"store_id": f"sim-{seed}-{i:03d}", "name": name, "city": c[0],
                    "flag": c[3], "lat": hlat, "lon": hlon, "wheel": wheel,
                    "rides": keen, "cruise": cruise,
                    "tz_off": int(round(c[2] / 15.0)) * 60, "rng": rng})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--riders", type=int, default=12)
    ap.add_argument("--days", type=int, default=90, help="how far back the history goes")
    ap.add_argument("--rides", type=int, default=0,
                    help="rides per rider (0 = pick from the rider's own keenness)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prefix", default="SIM", help="display-name prefix, so simulated riders "
                                                    "are never mistaken for real ones")
    ap.add_argument("--city", default="", help="restrict the fleet to one city")
    ap.add_argument("--list-cities", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="generate and report, upload nothing")
    a = ap.parse_args()

    if a.list_cities:
        for c in CITIES:
            print(f"{c[0]:<12} {c[1]:>9.4f} {c[2]:>10.4f}  {c[3]}")
        return 0
    if not a.dry_run and requests is None:
        return print("pip install requests") or 2

    riders = fleet(a.seed, a.riders, a.city)
    ok = flagged = failed = 0
    total_km = 0.0
    for r in riders:
        rng = r["rng"]
        name = f"{a.prefix}·{r['name']}"
        if not a.dry_run:
            register(a.base, r["store_id"], name, r["flag"])
        booked: list[tuple[datetime, datetime]] = []
        for _ in range(a.rides or r["rides"]):
            km = rng.uniform(4, 34)
            # One rider cannot be on two wheels at once, and the server knows it: a ride that
            # overlaps another of the same rider's is flagged `overlapping_trip`. Rolling a
            # random day per ride collided about once in 150, so the slot is checked first
            # rather than leaving one unexplained flag in the fleet.
            start = info = csv_text = None
            for _try in range(40):
                cand = (datetime.utcnow() - timedelta(days=rng.randrange(a.days))).replace(
                    hour=rng.randrange(7, 21), minute=rng.randrange(60),
                    second=0, microsecond=0)
                cand_end = cand + timedelta(hours=2)        # generous: the real end is shorter
                if all(cand_end <= b0 or cand >= b1 for b0, b1 in booked):
                    start = cand
                    break
            if start is None:
                continue                                    # a very busy rider; skip the ride
            csv_text, info = ride_csv(rng, r["lat"], r["lon"], start, km, r["cruise"])
            booked.append((start, start + timedelta(hours=2)))
            if a.dry_run:
                print(f"  {name:<22} {r['city']:<11} {info['km']:>6.1f} km "
                      f"{info['rows'] / 60:>5.0f} min  peak {info['peak_w'] / 1000:>4.1f} kW")
                ok += 1
                total_km += info["km"]
                continue
            res = upload(a.base, r["store_id"], csv_text, start, info["end"],
                         r["wheel"], r["tz_off"])
            st = res.get("validation_status") or res.get("error", "?")
            if st == "validated":
                ok += 1
                total_km += info["km"]
            elif st == "flagged":
                flagged += 1
            else:
                failed += 1
            extra = "  " + str(res.get("reasons")) if res.get("reasons") else ""
            print(f"  {name:<22} {r['city']:<11} {info['km']:>6.1f} km  {st}{extra}")
    print(f"\n{ok} validated, {flagged} flagged, {failed} failed · {total_km:,.0f} km")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
