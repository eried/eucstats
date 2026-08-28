#!/usr/bin/env python3
"""Dry-run the fall / free-spin detector over the stored trips. Writes nothing.

    cd /opt/eucstats && .venv/bin/python scripts/survey_anomalies.py
    cd /opt/eucstats && .venv/bin/python scripts/survey_anomalies.py --all --verbose

Before a detector is allowed to put a number on a public board, it has to be looked at on
real rides. This replays every trip's stored raw upload through ingest/anomalies.py and
reports what it would have counted, next to the count the old detector left in the database.

Falls and free spins are listed one per line, because those are the two the boards show and
the two a rider would dispute. Spikes and glitches are only totalled - they are the detector
saying "something is off in this log", not a claim about the rider.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _explain(db, cal, cap, prefix: str, anomalies) -> int:
    """Print the telemetry either side of every event, so a human can judge the verdict."""
    from ingest.parser import parse_csv
    from models import RawUpload, Trip
    from services.ingest import _gunzip_capped, _is_gzip

    t = next((x for x in db.query(Trip).all() if x.trip_uuid.startswith(prefix)), None)
    if t is None:
        print(f"no trip starting {prefix}")
        return 1
    ru = db.get(RawUpload, t.trip_uuid)
    data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
    samples = parse_csv(data.decode("utf-8", "replace"), 0)
    c = {**anomalies.DEFAULTS, **(cal or {})}
    has_motor = anomalies._has_motor_data(samples)
    needs_fix = any(x.lat is not None and x.lon is not None for x in samples)
    track = anomalies._track_kmh(samples)
    flags = anomalies._flag(samples, c, has_motor, needs_fix, track)
    print(f"{t.trip_uuid} {str(t.start_utc)[:16]} {len(samples)} samples, "
          f"has_motor={has_motor}, {(t.distance_km or 0):.1f} km")
    print()
    for start, end in anomalies._events(flags):
        ev = samples[start:end + 1]
        peak = max((x.speed for x in ev if x.speed is not None), default=0)
        before = anomalies._moving(samples, start - anomalies._CONTEXT, start, has_motor, c, track)
        after = anomalies._moving(samples, end + 1, end + 1 + anomalies._CONTEXT, has_motor, c, track)
        amps = [abs(x.current) for x in ev if x.current is not None]
        load = anomalies._median(amps) if amps else None
        idle = 1.0 if load is None or load < c["motor_active_a"] else 0.0
        prior = [x.speed for x in samples[max(0, start - anomalies._RUNUP):start] if x.speed is not None]
        from_rest = not prior or (anomalies._median(prior) < anomalies._MOVING_KMH)
        is_fall = (before is True and after is False
                   and len(ev) >= c["min_fall_len"]
                   and anomalies._span(ev) >= c["min_fall_s"])
        is_spin = (from_rest and after is not True and len(ev) >= c["min_event_len"]
                   and anomalies._span(ev) >= c["min_event_s"])
        around = (samples[max(0, start - anomalies._SURROUND):start]
                  + samples[end + 1:end + 1 + anomalies._SURROUND])
        worked = (not has_motor) or any(anomalies._drawing(x, c["motor_active_a"]) for x in around)
        if not (worked and peak >= c["free_spin_kmh"] and idle >= 0.7 and (is_fall or is_spin)):
            continue                              # only the ones that become a fall or a spin
        kind = "FALL" if is_fall else "SPIN"
        print(f"== {kind}  samples {start}-{end}  peak {peak:.1f} km/h  idle {idle:.0%}  "
              f"before_moving={before} after_moving={after} median_load={load}")
        for i in range(max(0, start - 6), min(len(samples), end + 7)):
            s = samples[i]
            mark = ">>" if start <= i <= end else "  "
            print(f"   {mark} {str(s.t)[11:19]} wheel={_f(s.speed):>6} gps={_f(s.gps_speed):>6} "
                  f"A={_f(s.current):>7} W={_f(s.power):>8} pwm={_f(s.pwm):>5} "
                  f"lat={_f(s.lat, 5):>10} lon={_f(s.lon, 5):>10}")
        print()
    return 0


SENSITIVITY_DOC = """
A detector that reports nothing is either right or broken, and no amount of staring at false
positives can tell those apart. So: take each real trip, splice a textbook event into it, and
see whether it comes back out. Specificity is what the survey measures; this is recall.

The splice is deliberately the EASY case - a clean, unambiguous event of the kind the boards
are meant to show. Anything the detector misses here it would certainly miss in the wild.
"""


def _at(samples, i, seconds):
    """Index `seconds` after sample i, so a splice lasts the same TIME on a 1 Hz log and a
    10 Hz one. Counting samples instead made every fast logger look like a miss."""
    t0 = samples[i].t
    for k in range(i, len(samples)):
        if (samples[k].t - t0).total_seconds() >= seconds:
            return k
    return len(samples)


def _inject_fall(samples, i):
    """A cutout at sample i: the wheel spins up unloaded while the rider goes down, then
    everything stops. Wheel accelerating past what it was doing, ground frozen, no current."""
    import copy
    out = copy.deepcopy(samples)
    base = out[i].speed or 30.0
    lat, lon = out[i].lat, out[i].lon
    spin_end, stop_end = _at(out, i, 8), _at(out, i, 28)
    for k in range(i, spin_end):                    # the wheel runs away, unloaded
        s = out[k]
        s.speed = base * (1.3 + 0.03 * (k - i))
        s.gps_speed, s.current, s.power = 0.0, 0.1 + (k % 3) * 0.05, None
        s.lat, s.lon = lat, lon
    for k in range(spin_end, stop_end):             # rider on the ground, wheel stopped
        s = out[k]
        s.speed, s.gps_speed = 0.0, 0.0
        s.current, s.power = 0.05 + (k % 3) * 0.05, None
        s.lat, s.lon = lat, lon
    return out


def _inject_spin(samples, i):
    """A free spin at sample i: parked, then the wheel turns with nothing on it, then parked
    again. Harmless - the point is that it must be told apart from a fall, not missed."""
    import copy
    out = copy.deepcopy(samples)
    lat, lon = out[i].lat, out[i].lon
    a, b, end = _at(out, i, 8), _at(out, i, 20), _at(out, i, 30)
    for k in range(i, end):
        s = out[k]
        spinning = a <= k < b
        s.speed = 25.0 if spinning else 0.0
        s.gps_speed = 0.0
        # A parked controller idles noisily; that jitter is what proves the channel is live.
        s.current, s.power = (0.2 if spinning else 0.08) + (k % 3) * 0.03, None
        s.lat, s.lon = lat, lon
    return out


def _splice_point(samples):
    """A moment the rider is genuinely under way, not the first sample above 20 km/h.

    That first sample is by construction the one right after every trip's opening launch, so
    the ten samples before it are still the standstill - the context honestly reads "was not
    travelling", and one spliced fall in seven was thrown away for a reason that says nothing
    about the detector.
    """
    run = 0
    for k in range(len(samples) - 40):
        s = samples[k]
        if (s.speed or 0) > 15:
            run += 1
        else:
            run = 0
        if run >= 15 and s.lat is not None and s.current is not None:
            return k
    return None


def _sensitivity(db, cal, cap, kind: str, everything: bool, detect, one=None) -> int:
    """Splice one synthetic event into every usable trip and count how many come back."""
    from ingest.parser import parse_csv
    from models import RawUpload, Trip
    from services.ingest import _gunzip_capped, _is_gzip

    print(SENSITIVITY_DOC)
    want = "fall" if kind == "fall" else "spin"
    inject = _inject_fall if kind == "fall" else _inject_spin
    if one:                                       # a single trip, with the event's evidence
        t = next(x for x in db.query(Trip).all() if x.trip_uuid.startswith(one))
        ru = db.get(RawUpload, t.trip_uuid)
        data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
        samples = parse_csv(data.decode("utf-8", "replace"), 0)
        i = _splice_point(samples)
        tr = []
        print(f"{t.trip_uuid[:8]}: spliced a {kind} in at sample {i} of {len(samples)}")
        print(f"result: {detect(inject(samples, i), cal, trace=tr)}")
        for e in tr:
            if abs(e["start"] - i) < 40:
                print(f"   samples {e['start']}-{e['end']} n={e['n']} span={e['span']:.1f}s "
                      f"peak={e['peak']:.1f} load={e['load']} unloaded={e['unloaded']} "
                      f"before={e['before']} after={e['after']} worked={e['worked']}")
        return 0
    q = db.query(Trip)
    if not everything:
        q = q.filter(Trip.validation_status == "validated")
    found = missed = skipped = 0
    misses, why_tot = [], {}
    for t in q.order_by(Trip.start_utc).all():
        ru = db.get(RawUpload, t.trip_uuid)
        if ru is None:
            continue
        try:
            data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
            samples = parse_csv(data.decode("utf-8", "replace"), 0)
        except Exception:
            continue
        i = _splice_point(samples)
        if i is None:
            skipped += 1
            continue
        before = detect(samples, cal)[want]
        tr = []
        after = detect(inject(samples, i), cal, trace=tr)[want]
        if after > before:
            found += 1
        else:
            missed += 1
            # Grade the spliced event itself: a miss is only worth acting on once you know
            # which test threw it away.
            near = {}
            for e in tr:
                if e["start"] <= i <= e["end"] or i <= e["start"] <= i + 30:
                    _grade(e, near)
            why = max(near, key=near.get) if near else "never even flagged"
            why_tot[why] = why_tot.get(why, 0) + 1
            if len(misses) < 8:
                misses.append(f"   {t.trip_uuid[:8]} at sample {i}: {why}")
    total = found + missed
    if misses:
        print(f"MISSED ({missed}) - a sample of them, then what threw each one away:")
        print(chr(10).join(misses))
        for why, n in sorted(why_tot.items(), key=lambda kv: -kv[1]):
            print(f"   {n:5d}  {why}")
    rate = (100.0 * found / total) if total else 0.0
    print(f"{kind}: found in {found}/{total} trips ({rate:.0f}%), "
          f"{skipped} trip(s) had no suitable place to splice one")
    return 0


def _module(path):
    """The candidate anomalies module itself, for the internals --trip needs to show."""
    import ingest.anomalies as deployed
    if not path:
        return deployed
    import importlib.util
    import ingest                                 # so the candidate's relative imports resolve
    spec = importlib.util.spec_from_file_location("ingest.anomalies_candidate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _detector(path):
    """The detect() under test. Surveying a candidate must never mean copying it over the
    deployed file: the site is serving from that tree, and a survey is a read-only question."""
    return _module(path).detect


def _grade(e, near, bucket=None, uuid=None):
    """The FIRST gate an event failed, so the histogram reads as a funnel rather than a tally
    of overlapping conditions. Only events that got past every earlier gate are counted
    against a later one."""
    def bump(k):
        near[k] = near.get(k, 0) + 1
        if bucket is not None and ("SHAPED" in k or "context unknown" in k):
            bucket.append((uuid, k, e))

    if not e["worked"]:
        return bump("motor never worked nearby -> glitch")
    if not e["unloaded"]:
        return bump("motor was loaded (a rider was on it)")
    if e["before"] is None or e["after"] is None:
        return bump(f"context unknown (before={e['before']} after={e['after']})")
    if e["before"] and e["after"]:
        return bump("travelling before AND after: rode straight through it")
    if not e["before"] and e["after"]:
        return bump("stopped, then moving off: a normal launch")
    if e["before"] and not e["after"]:                  # fall-shaped
        if e["n"] < 3:
            return bump(f"FALL-SHAPED but only {e['n']} sample(s)")
        return bump(f"FALL-SHAPED but only {e['span']:.1f}s long")
    if e["n"] < 3:
        return bump(f"SPIN-SHAPED but only {e['n']} sample(s)")
    return bump(f"SPIN-SHAPED but only {e['span']:.1f}s long")


def _fixture(db, cap, prefix: str, lo: int, hi: int) -> int:
    """Emit a real sample range as a Python literal, so a regression test is built from the
    telemetry that actually fooled the detector rather than from a guess at what it looked
    like. Reduced fixtures kept passing for reasons the real trip did not share."""
    from ingest.parser import parse_csv
    from models import RawUpload, Trip
    from services.ingest import _gunzip_capped, _is_gzip

    t = next((x for x in db.query(Trip).all() if x.trip_uuid.startswith(prefix)), None)
    ru = db.get(RawUpload, t.trip_uuid)
    data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
    samples = parse_csv(data.decode("utf-8", "replace"), 0)
    t0 = samples[lo].t
    print(f"# {t.trip_uuid[:8]} samples {lo}-{hi}")
    print("# (dt, wheel, gps, amps, lat, lon)")
    for s in samples[lo:hi + 1]:
        print(f"    ({(s.t - t0).total_seconds():.0f}, {_f(s.speed)}, {_f(s.gps_speed)}, "
              f"{_f(s.current)}, {_f(s.lat, 5)}, {_f(s.lon, 5)}),".replace("-,", "None,")
              .replace("= -", "=None"))
    return 0


def _f(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", help="path to the anomalies.py to test, instead of the "
                                      "deployed one - so a survey never edits the live tree")
    ap.add_argument("--all", action="store_true",
                    help="include flagged/rejected trips (default: validated only)")
    ap.add_argument("--verbose", action="store_true", help="list every trip, not just the hits")
    ap.add_argument("--trip", help="dump the samples around each fall/spin of one trip")
    ap.add_argument("--fixture", help="TRIP:LO:HI - emit that sample range as a test fixture")
    ap.add_argument("--inject", choices=("fall", "spin"),
                    help="splice a synthetic event into every real trip and see if it is found")
    args = ap.parse_args()

    import config
    from database import SessionLocal
    detect = _detector(args.detector)
    from ingest.parser import parse_csv
    from models import RawUpload, Rider, Trip
    from services import settings
    from services.ingest import _gunzip_capped, _is_gzip

    db = SessionLocal()
    try:
        cal = settings.get_calibration(db)
        cap = int(config.MAX_DECOMPRESSED_MB * 1024 * 1024)
        if args.inject:
            return _sensitivity(db, cal, cap, args.inject, args.all, detect, args.trip)
        if args.fixture:
            u, lo, hi = args.fixture.split(":")
            return _fixture(db, cap, u, int(lo), int(hi))
        if args.trip:
            return _explain(db, cal, cap, args.trip, _module(args.detector))
        q = db.query(Trip)
        if not args.all:
            q = q.filter(Trip.validation_status == "validated")
        trips = q.order_by(Trip.start_utc).all()
        names = {r.store_id: r.display_name for r in db.query(Rider).all()}
        print(f"{len(trips)} trip(s) to survey\n")

        tot = {"fall": 0, "spin": 0, "spike": 0, "glitch": 0}
        near, misses = {}, []
        hits, no_raw, no_current, no_gps, old_only = [], 0, 0, 0, []
        for t in trips:
            ru = db.get(RawUpload, t.trip_uuid)
            if ru is None:
                no_raw += 1
                continue
            try:
                data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
                samples = parse_csv(data.decode("utf-8", "replace"), 0)
            except Exception as e:
                print(f"  {t.trip_uuid[:8]} unreadable: {e}")
                continue
            if not any(s.current is not None for s in samples):
                no_current += 1
            if not any(s.gps_speed is not None for s in samples):
                no_gps += 1
            tr = []
            r = detect(samples, cal, trace=tr)
            for e in tr:
                _grade(e, near, misses, t.trip_uuid)
            for k in tot:
                tot[k] += r[k]
            who = (names.get(t.rider_store_id) or "?")[:14].ljust(14)
            head = (f"  {t.trip_uuid[:8]} {who} {str(t.start_utc)[:10]} "
                    f"{(t.distance_km or 0):7.1f} km {(t.max_speed or 0):5.1f} km/h")
            line = (f"{head}  fall={r['fall']} spin={r['spin']} "
                    f"spike={r['spike']:4d} glitch={r['glitch']:3d}  (stored cutouts={t.cutout_count or 0})")
            if r["fall"] or r["spin"]:
                hits.append(line)
            elif (t.cutout_count or 0):
                old_only.append(line)
            if args.verbose:
                print(line)

        if hits:
            print(f"\nFALLS / FREE SPINS ({len(hits)} trip(s)):")
            for h in hits:
                print(h)
        if old_only:
            print(f"\nold detector counted cutouts, new one sees none ({len(old_only)}):")
            for h in old_only[:20]:
                print(h)

        if misses:
            print("\nnear misses - everything that got past the load and context tests:")
            for uuid, why, e in misses:
                print(f"   {uuid[:8]} samples {e['start']}-{e['end']}  peak {e['peak']:5.1f} km/h  "
                      f"load {('-' if e['load'] is None else format(e['load'], '.1f')):>5} A  {why}")
        print("\nwhy events were not counted (the first test each one failed):")
        for reason, n in sorted(near.items(), key=lambda kv: -kv[1]):
            print(f"   {n:6d}  {reason}")
        print(f"\ntotals: falls={tot['fall']} lifts={tot['lift']} "
              f"spikes={tot['spike']} glitches={tot['glitch']}")
        print(f"coverage: {no_current} trip(s) with no current channel, {no_gps} with no GPS speed, "
              f"{no_raw} with the raw upload already evicted")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
