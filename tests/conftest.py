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


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """The crews config is cached in-process; `db` wipes the table underneath it."""
    from services import settings
    settings._invalidate_crews_cache()
    yield
    settings._invalidate_crews_cache()


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


@pytest.fixture(autouse=True)
def _public_handle_matches_store_id():
    """In tests, a rider's public handle IS their store_id.

    Production gives every rider a random opaque handle (models._give_rider_a_public_id),
    because the public API publishes it in place of the store_id. That is the right behaviour
    and the wrong thing to assert against: the fixtures here name riders "a", "b", "gone", and
    every board assertion reads better comparing those than resolving a random hex each time.
    So the handle is pinned to the store_id for the duration of a test, and the production
    listener is left to do its job everywhere else.
    """
    import models
    from sqlalchemy import event

    def _pin(mapper, connection, target):
        target.public_id = target.store_id

    event.listen(models.Rider, "before_insert", _pin)
    try:
        yield
    finally:
        event.remove(models.Rider, "before_insert", _pin)
