"""eucstats configuration. Override any value via EUCSTATS_* environment vars."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("EUCSTATS_DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "eucstats.sqlite"
ADMIN_STATE_FILE = DATA_DIR / "admin.json"
# Site-level settings that must NOT travel with a dataset (e.g. test-mode banner).
SITE_STATE_FILE = DATA_DIR / "site.json"

# --- Retention (full-resolution raw uploads only; summaries/tracks are permanent) ---
RETENTION_DAYS = int(os.environ.get("EUCSTATS_RETENTION_DAYS", "30"))
DISK_FLOOR_GB = float(os.environ.get("EUCSTATS_DISK_FLOOR_GB", "10"))
RETENTION_INTERVAL_S = int(os.environ.get("EUCSTATS_RETENTION_INTERVAL_S", "3600"))

# --- Attestation: "stub" (accept all) or "enforce" (require valid Play Integrity) ---
ATTESTATION_MODE = os.environ.get("EUCSTATS_ATTESTATION_MODE", "stub")
ANDROID_PACKAGE = os.environ.get("EUCSTATS_ANDROID_PACKAGE", "com.eried.eucplanet")

# --- Analytics: Microsoft Clarity project id. Empty disables the tag entirely.
#     Set via env so the id stays out of the public repo and can change without a code deploy. ---
CLARITY_ID = os.environ.get("EUCSTATS_CLARITY_ID", "").strip()

# --- Ingest allowlist: if set (comma-separated store_ids), ONLY those riders'
#     uploads are accepted (everyone else gets 403). Empty = open to all
#     registered riders. Use it to keep the live site to your own submissions. ---
INGEST_ALLOW = [s.strip() for s in os.environ.get("EUCSTATS_INGEST_ALLOW", "").split(",") if s.strip()]

# --- Map grid: zoom levels expressed as cell size in degrees (coarse -> fine) ---
GRID_ZOOMS = [float(z) for z in os.environ.get("EUCSTATS_GRID_ZOOMS", "2.0,0.5,0.1").split(",")]

# --- Limits ---
MAX_UPLOAD_MB = float(os.environ.get("EUCSTATS_MAX_UPLOAD_MB", "8"))
MAX_DECOMPRESSED_MB = float(os.environ.get("EUCSTATS_MAX_DECOMPRESSED_MB", "64"))   # gzip-bomb guard
MAX_SAMPLES = int(os.environ.get("EUCSTATS_MAX_SAMPLES", "300000"))                  # per-trip sample cap
AVATAR_PX = 64
TRACK_MAX_POINTS = 500

# --- Plausibility thresholds (env-overridable so live tuned values stay off the
#     public repo; the repo only ships sensible defaults) ---
MAX_KMH = float(os.environ.get("EUCSTATS_MAX_KMH", "120"))            # sample speed cap -> flag
MAX_G = float(os.environ.get("EUCSTATS_MAX_G", "12"))                 # |G-Force| cap -> flag
TELEPORT_KMH = float(os.environ.get("EUCSTATS_TELEPORT_KMH", "150"))  # implied speed between GPS fixes
TELEPORT_MAX_JUMPS = int(os.environ.get("EUCSTATS_TELEPORT_MAX_JUMPS", "8"))  # tolerate isolated spikes
DIST_TOLERANCE = float(os.environ.get("EUCSTATS_DIST_TOLERANCE", "0.4"))      # odometer-vs-gps mismatch
UNVERIFIED_DIST_KM = float(os.environ.get("EUCSTATS_UNVERIFIED_DIST_KM", "3.0"))  # flag long rides with no GPS at all

# --- Telemetry calibration (physics limits used when summarizing a trip) ---
MAX_ACCEL_KMH_S = float(os.environ.get("EUCSTATS_MAX_ACCEL_KMH_S", "20"))     # believable accel; faster rise = freespin/spike, not real speed
SUSTAIN_SECS = float(os.environ.get("EUCSTATS_SUSTAIN_SECS", "2"))            # window for "sustained" power/current/g-force metrics
FREESPIN_MARGIN_KMH = float(os.environ.get("EUCSTATS_FREESPIN_MARGIN_KMH", "5"))  # raw speed must beat realistic by this to count as a freespin
ASCENT_HYSTERESIS_M = float(os.environ.get("EUCSTATS_ASCENT_HYSTERESIS_M", "3"))  # ignore elevation wiggles under this (GPS noise)
ODO_MAX_STEP_KM = float(os.environ.get("EUCSTATS_ODO_MAX_STEP_KM", "5"))      # reject odometer jumps bigger than this per reading
SAG_WINDOW_S = float(os.environ.get("EUCSTATS_SAG_WINDOW_S", "5"))            # voltage-sag look-back window
ACCEL_TARGET_KMH = float(os.environ.get("EUCSTATS_ACCEL_TARGET_KMH", "40"))   # launch metric target speed (0 -> target)
ACCEL_MIN_S = float(os.environ.get("EUCSTATS_ACCEL_MIN_S", "1.5"))            # launches faster than this are sensor noise
ACCEL_MAX_S = float(os.environ.get("EUCSTATS_ACCEL_MAX_S", "20"))             # only count a launch reaching target within this
SUSTAIN_ACCEL_LO_S = float(os.environ.get("EUCSTATS_SUSTAIN_ACCEL_LO_S", "2"))  # sustained-acceleration min window
SUSTAIN_ACCEL_HI_S = float(os.environ.get("EUCSTATS_SUSTAIN_ACCEL_HI_S", "6"))  # sustained-acceleration max window
RANGE_MIN_BATTERY_PCT = float(os.environ.get("EUCSTATS_RANGE_MIN_BATTERY_PCT", "10"))  # min battery drop to estimate full-charge range
MISMATCH_MIN_KM = float(os.environ.get("EUCSTATS_MISMATCH_MIN_KM", "0.5"))    # min distance before odo-vs-GPS mismatch is judged

# --- Rate limits (per hour; 0 disables a given limit) ---
RATE_RIDER_CREATE_PER_IP = int(os.environ.get("EUCSTATS_RATE_RIDER_CREATE_PER_IP", "20"))  # new accounts / hour / IP
RATE_TRIP_PER_RIDER = int(os.environ.get("EUCSTATS_RATE_TRIP_PER_RIDER", "60"))            # uploads / hour / rider
RATE_TRIP_PER_IP = int(os.environ.get("EUCSTATS_RATE_TRIP_PER_IP", "200"))                 # uploads / hour / IP
