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

# --- Attestation: "stub" (accept all) or "enforce" (require valid Play Integrity) ---
ATTESTATION_MODE = os.environ.get("EUCSTATS_ATTESTATION_MODE", "stub")
ANDROID_PACKAGE = os.environ.get("EUCSTATS_ANDROID_PACKAGE", "com.eried.eucplanet")

# --- Map grid: zoom levels expressed as cell size in degrees (coarse -> fine) ---
GRID_ZOOMS = [float(z) for z in os.environ.get("EUCSTATS_GRID_ZOOMS", "2.0,0.5,0.1").split(",")]

# --- Limits ---
MAX_UPLOAD_MB = float(os.environ.get("EUCSTATS_MAX_UPLOAD_MB", "8"))
AVATAR_PX = 64
TRACK_MAX_POINTS = 500

# --- Plausibility thresholds ---
MAX_KMH = 120.0          # any sample speed above this -> flagged
MAX_G = 12.0             # any |G-Force| above this -> flagged
TELEPORT_KMH = 150.0     # implied speed between consecutive GPS fixes
DIST_TOLERANCE = 0.4     # allowed |odometer - gps| / odometer mismatch
