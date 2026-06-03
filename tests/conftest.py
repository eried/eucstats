"""Test bootstrap: isolate each run to a fresh temp data dir before app import."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Must be set BEFORE config/database/main are imported by any test.
os.environ["EUCSTATS_DATA_DIR"] = tempfile.mkdtemp(prefix="eucstats-test-")
os.environ.setdefault("EUCSTATS_ATTESTATION_MODE", "stub")

import pytest


@pytest.fixture
def db():
    from database import SessionLocal, init_db
    init_db()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
