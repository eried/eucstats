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


@pytest.fixture(autouse=True)
def _clear_ratelimit():
    # the limiter is a process-wide in-memory store; reset it between tests
    try:
        from services import ratelimit
        ratelimit.clear()
    except Exception:
        pass
    yield


@pytest.fixture
def db():
    # Fresh schema per test — materialized tables (records) use global keys,
    # so tests must not bleed into one another.
    from database import SessionLocal, engine, Base, init_db
    init_db()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
