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

import hashlib

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


def HANDLE(store_id: str) -> str:
    """The handle a test rider gets: deterministic, and containing nothing of the store_id.

    This was `h-{store_id}`, which a reviewer's finding showed to be the wrong shape. They
    found a real row on the dev machine whose handle was exactly that -- written there by a
    stray script running this fixture against the real database -- read the handle out of the
    API, guessed the store_id by stripping the prefix, and minted a working session with it.
    `_handle` now re-mints any handle that contains the store_id it stands for, so a fixture
    producing that shape would be a fixture testing something the code refuses to do.
    """
    return "h" + hashlib.sha1(store_id.encode()).hexdigest()[:12]


@pytest.fixture(autouse=True)
def _public_handle_matches_store_id():
    """In tests, a rider's public handle is derived from their store_id but is not equal to it.

    Production gives every rider a random opaque handle (models._give_rider_a_public_id),
    because the public API publishes it in place of the store_id. That is the right behaviour
    and the wrong thing to assert against: the fixtures here name riders "a", "b", "gone", and
    every board assertion reads better comparing those than resolving a random hex each time.

    This used to pin the handle to the store_id exactly, which made the fixtures readable and
    made the test guarding the credential leak compare a value against itself -- it could not
    go red however badly the API leaked. A prefix keeps both properties: "h-a" is as readable
    as "a" in an assertion, and a store_id published where a handle belongs is now a
    different string, so a leak is something a test can actually see.
    """
    import models
    from sqlalchemy import event

    def _pin(mapper, connection, target):
        target.public_id = HANDLE(target.store_id)

    event.listen(models.Rider, "before_insert", _pin)
    try:
        yield
    finally:
        event.remove(models.Rider, "before_insert", _pin)
