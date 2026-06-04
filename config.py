"""eucstats configuration. Override any value via EUCSTATS_* environment vars."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("EUCSTATS_DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "eucstats.sqlite"
ADMIN_STATE_FILE = DATA_DIR / "admin.json"

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
AVATAR_PX = 64
TRACK_MAX_POINTS = 500

# --- Plausibility thresholds (env-overridable so live tuned values stay off the
#     public repo; the repo only ships sensible defaults) ---
MAX_KMH = float(os.environ.get("EUCSTATS_MAX_KMH", "120"))            # sample speed cap -> flag
MAX_G = float(os.environ.get("EUCSTATS_MAX_G", "12"))                 # |G-Force| cap -> flag
TELEPORT_KMH = float(os.environ.get("EUCSTATS_TELEPORT_KMH", "150"))  # implied speed between GPS fixes
TELEPORT_MAX_JUMPS = int(os.environ.get("EUCSTATS_TELEPORT_MAX_JUMPS", "8"))  # tolerate isolated spikes
DIST_TOLERANCE = float(os.environ.get("EUCSTATS_DIST_TOLERANCE", "0.4"))      # odometer-vs-gps mismatch
