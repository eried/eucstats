"""Ingest orchestration: attestation -> dedupe -> parse -> summarize -> geo ->
plausibility -> persist (summary + track + raw) -> aggregate."""
from __future__ import annotations

import gzip
import hashlib
import zlib
from datetime import datetime

from sqlalchemy.exc import IntegrityError

import config
from ingest.attestation import get_verifier
from ingest.downsample import downsample, encode_track
from ingest.geo import cells_for, country_of
from ingest.parser import parse_csv
from ingest.plausibility import check
from services import settings
from ingest.summary import summarize
from models import Trip, Wheel, utcnow
from repository.riders import RiderRepo
from repository.trips import TripRepo
from services.aggregator import Aggregator


class IngestError(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _is_gzip(b: bytes) -> bool:
    return len(b) >= 2 and b[0] == 0x1F and b[1] == 0x8B


def _gunzip_capped(raw: bytes, cap: int) -> bytes:
    """Decompress gzip with a hard output cap — defends against gzip bombs."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(raw, cap + 1)
    if d.unconsumed_tail or len(out) > cap:
        raise IngestError(413, "decompressed_too_large")
    out += d.flush()
    if len(out) > cap:
        raise IngestError(413, "decompressed_too_large")
    return out


def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None else None


def _intval(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _verdict(status: str) -> str:
    """Map internal validation_status to the app-facing verdict."""
    return {"validated": "accepted", "flagged": "under_review",
            "rejected": "rejected"}.get(status, "under_review")


def _parse_iso(s):
    """Parse the app's authoritative ISO-8601 UTC timestamps ('...Z') to naive UTC."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class IngestService:
    def __init__(self, db):
        self.db = db
        self.trips = TripRepo(db)
        self.riders = RiderRepo(db)
        self.verifier = get_verifier(config.ATTESTATION_MODE, config.ANDROID_PACKAGE)

    def handle(self, meta: dict, raw_bytes: bytes) -> dict:
        res = self.verifier.verify(meta)
        if not res.ok:
            raise IngestError(401, f"attestation_failed:{res.reason}")

        store = meta.get("store_id")
        trip_uuid = meta.get("trip_uuid")
        if not store or not trip_uuid:
            raise IngestError(400, "missing_store_id_or_trip_uuid")
        allow = settings.ingest_allow(self.db)
        if allow["enabled"] and allow["ids"] and store not in allow["ids"]:
            raise IngestError(403, "rider_not_allowlisted")
        if self.riders.get(store) is None:
            raise IngestError(400, "rider_not_registered")

        existing = self.trips.get(trip_uuid)
        if existing is not None:
            return {"trip_uuid": trip_uuid,
                    "validation_status": existing.validation_status,
                    "verdict": _verdict(existing.validation_status),
                    "duplicate": True}

        cap = int(config.MAX_DECOMPRESSED_MB * 1024 * 1024)
        try:
            if _is_gzip(raw_bytes):
                data = _gunzip_capped(raw_bytes, cap)
            else:
                if len(raw_bytes) > cap:
                    raise IngestError(413, "payload_too_large")
                data = raw_bytes
            text = data.decode("utf-8", "replace")
        except IngestError:
            raise
        except Exception as e:
            raise IngestError(422, f"decompress_failed:{e}")

        # content integrity: the app sends sha256 of the *uncompressed* CSV
        want_sha = meta.get("file_sha256")
        if want_sha and hashlib.sha256(data).hexdigest() != str(want_sha).strip().lower():
            raise IngestError(422, "checksum_mismatch")

        try:
            tz_off = int(meta.get("tz_offset_min", 0) or 0)
        except (TypeError, ValueError):
            tz_off = 0
        tz_off = max(-840, min(840, tz_off))   # clamp to +-14h (untrusted client value)
        try:
            samples = parse_csv(text, tz_off)
        except Exception as e:
            raise IngestError(422, f"parse_failed:{e}")
        if not samples:
            raise IngestError(422, "no_samples")
        if len(samples) > config.MAX_SAMPLES:
            raise IngestError(413, "too_many_samples")

        sm = summarize(samples, max_step_km=5.0, gps_tolerance=config.DIST_TOLERANCE)
        # prefer the app's authoritative UTC start/end (the CSV Date is local, no TZ)
        m_start = _parse_iso(meta.get("start_utc")) or _naive(sm.start_utc)
        m_end = _parse_iso(meta.get("end_utc")) or _naive(sm.end_utc)
        is_mock = bool(meta.get("is_mock_location", False))
        status, reasons = check(
            samples, sm, is_mock,
            max_kmh=config.MAX_KMH, max_g=config.MAX_G,
            teleport_kmh=config.TELEPORT_KMH, teleport_max_jumps=config.TELEPORT_MAX_JUMPS,
            dist_tolerance=config.DIST_TOLERANCE, unverified_dist_km=config.UNVERIFIED_DIST_KM,
        )

        # same rider with a time-overlapping (non-rejected) trip is physically
        # impossible — one person, two wheels at once — and also catches the same
        # ride uploaded from two phones (different trip_uuid, same window).
        if m_start and m_end:
            s0, e0 = m_start, m_end
            clash = (self.db.query(Trip.trip_uuid)
                     .filter(Trip.rider_store_id == store, Trip.trip_uuid != trip_uuid,
                             Trip.validation_status != "rejected",
                             Trip.start_utc.isnot(None), Trip.end_utc.isnot(None),
                             Trip.start_utc < e0, Trip.end_utc > s0)
                     .first())
            if clash and "overlapping_trip" not in reasons:
                reasons.append("overlapping_trip")
        status = "flagged" if reasons else status

        first = next((s for s in samples if s.lat is not None and s.lon is not None), None)
        country = start_lat = start_lon = start_cell = None
        if first:
            start_lat, start_lon = first.lat, first.lon
            country = country_of(start_lat, start_lon)
            cells = cells_for(start_lat, start_lon, config.GRID_ZOOMS)
            start_cell = cells.get(min(config.GRID_ZOOMS)) if cells else None

        wheel = meta.get("wheel") or {}
        wid = wheel.get("serial") or wheel.get("ble_mac")
        self._register_wheel(store, wheel, wid)

        try:
            trip = self.trips.insert_trip(
                trip_uuid=trip_uuid, rider_store_id=store, wheel_id=wid,
                start_utc=m_start, end_utc=m_end,
                tz=str(meta.get("tz") or tz_off), tz_known=bool(meta.get("tz_known", True)),
                distance_km=sm.distance_km, duration_s=sm.duration_s,
                max_speed=sm.max_speed, avg_speed=sm.avg_speed, max_gforce=sm.max_gforce,
                wh_per_km=sm.wh_per_km, max_sustained_w=sm.max_sustained_w,
                max_sustained_a=sm.max_sustained_a, peak_voltage=sm.peak_voltage,
                fastest_0_40_s=sm.fastest_0_40_s,
                ascent_m=sm.ascent_m, alt_range_m=sm.alt_range_m,
                battery_used_pct=sm.battery_used_pct, est_range_km=sm.est_range_km,
                country=country, start_cell=start_cell, start_lat=start_lat, start_lon=start_lon,
                validation_status=status, flag_reasons=reasons or None,
                schema_version=meta.get("schema_version"), source_app=meta.get("source_app"),
                is_mock_location=is_mock, sample_count=sm.sample_count,
                app_version=meta.get("app_version"),
                os_name=("ios" if meta.get("platform") == "apple" else "android"),
                meta_json=({k: meta.get(k) for k in ("gps", "os_version", "sample_count")
                            if meta.get(k) is not None} or None),
            )
        except IntegrityError:
            # a concurrent upload of the same trip_uuid won the insert race -> idempotent duplicate
            self.db.rollback()
            ex = self.trips.get(trip_uuid)
            vs = ex.validation_status if ex else "flagged"
            return {"trip_uuid": trip_uuid, "validation_status": vs,
                    "verdict": _verdict(vs), "duplicate": True}

        track = downsample(samples, config.TRACK_MAX_POINTS)
        if track:
            self.trips.save_track(trip_uuid, encode_track(track))
        self.trips.save_raw(trip_uuid, raw_bytes)

        if status == "validated":
            Aggregator(self.db).apply(trip)

        return {"trip_uuid": trip_uuid, "validation_status": status,
                "verdict": _verdict(status), "reasons": reasons, "duplicate": False,
                "distance_km": round(sm.distance_km, 3), "country": country}

    def _register_wheel(self, store, wheel: dict, wid):
        if not wid:
            return
        w = self.db.get(Wheel, wid)
        if w is None:
            self.db.add(Wheel(
                wheel_id=wid, rider_store_id=store, brand=wheel.get("brand"),
                model=wheel.get("model"), ble_name=wheel.get("ble_name"),
                firmware=wheel.get("firmware"),
            ))
        else:
            w.last_seen = utcnow()
        self.db.commit()
