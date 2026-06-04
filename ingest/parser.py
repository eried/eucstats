"""Header-driven CSV parser for EUC trip logs.

Maps columns by NAME (not position), tolerates missing columns, and handles
both date formats seen in real data:
  - ISO:     2025-05-30T20:17:22.000000   (DarknessBot / euc.world)
  - dotted:  01.06.2026 20:24:31.204      (eucplanet native)
Neither carries a timezone, so the caller supplies the trip's UTC offset.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# header (lowercased) -> canonical field name
CANON = {
    "date": "t",
    "speed": "speed", "gps speed": "gps_speed", "ext gps speed": "ext_gps_speed",
    "voltage": "voltage", "current": "current", "power": "power", "pwm": "pwm",
    "battery level": "battery", "total mileage": "odo", "temperature": "temp",
    "altitude": "alt", "latitude": "lat", "longitude": "lon",
    "g-force": "g", "g-force x": "gx", "g-force y": "gy",
    "pitch": "pitch", "roll": "roll",
}

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S",
)


@dataclass
class Sample:
    t: datetime
    lat: float | None = None
    lon: float | None = None
    speed: float | None = None
    gps_speed: float | None = None
    ext_gps_speed: float | None = None
    alt: float | None = None
    odo: float | None = None
    voltage: float | None = None
    current: float | None = None
    power: float | None = None
    pwm: float | None = None
    battery: float | None = None
    temp: float | None = None
    g: float | None = None
    gx: float | None = None
    gy: float | None = None
    pitch: float | None = None
    roll: float | None = None


def _parse_dt(s: str, tz_offset_min: int) -> datetime:
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            naive_local = datetime.strptime(s, fmt)
            return (naive_local - timedelta(minutes=tz_offset_min)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognized date format: {s!r}")


def _f(v: str | None) -> float | None:
    v = (v or "").strip()
    if v in ("", "-"):
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    # reject NaN / +-inf — they poison max()/min()/sum() and aren't valid JSON
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def parse_csv(text: str, tz_offset_min: int = 0) -> list[Sample]:
    """Parse trip CSV text into normalized samples. `tz_offset_min` converts the
    file's local wall-clock to UTC (UTC = local - offset)."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return []
    idx = {CANON[h]: i for i, h in enumerate(header) if h in CANON}
    if "t" not in idx:
        raise ValueError("CSV has no recognizable Date column")

    out: list[Sample] = []
    ti = idx["t"]
    for row in reader:
        # tolerate ragged rows: require a date cell; missing trailing columns -> None
        if not row or ti >= len(row) or not (row[ti] or "").strip():
            continue

        def g(key: str) -> float | None:
            i = idx.get(key)
            return _f(row[i]) if (i is not None and i < len(row)) else None

        _lat = g("lat")
        _lon = g("lon")
        if _lat is not None and _lon is not None and abs(_lat) < 1e-7 and abs(_lon) < 1e-7:
            _lat = _lon = None   # 0,0 = "no GPS fix" sentinel, not a real coordinate
        out.append(Sample(
            t=_parse_dt(row[idx["t"]], tz_offset_min),
            lat=_lat, lon=_lon, speed=g("speed"), gps_speed=g("gps_speed"),
            ext_gps_speed=g("ext_gps_speed"), alt=g("alt"), odo=g("odo"),
            voltage=g("voltage"), current=g("current"), power=g("power"), pwm=g("pwm"),
            battery=g("battery"), temp=g("temp"), g=g("g"), gx=g("gx"), gy=g("gy"),
            pitch=g("pitch"), roll=g("roll"),
        ))
    return out
