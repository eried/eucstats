"""SQLite (WAL) engine + session factory, behind SQLAlchemy."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

import config

Base = declarative_base()

engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    # WAL lets readers run during a write, but writers still take turns, and the territory
    # rebuild holds one transaction from the moment it clears the old cells to the moment it
    # commits the new ones. Python's default is to give up after five seconds, which would
    # turn "a rebuild is running" into a failed trip upload for a rider who was simply
    # unlucky with the timing. Thirty seconds of waiting is invisible; a lost upload is not.
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI dependency yielding a session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the original schema. SQLite's create_all() will NOT add
# columns to an existing table, so we ALTER them in idempotently (also when an
# older dataset snapshot is swapped in). type is the SQLite column affinity.
NEW_COLUMNS = {
    "trips": [("max_freespin", "FLOAT"), ("max_voltage_sag", "FLOAT"),
              ("sustained_accel", "FLOAT"),
              ("max_altitude_m", "FLOAT"), ("min_altitude_m", "FLOAT"),
              ("max_temp", "FLOAT"), ("min_temp", "FLOAT"),
              ("temp_rise_rate", "FLOAT"), ("temp_drop_rate", "FLOAT"),
              ("max_pwm", "FLOAT"), ("min_battery_pct", "FLOAT"),
              ("g_sust_4s", "FLOAT"), ("g_sust_6s", "FLOAT"), ("pwm_sust_3s", "FLOAT"),
              ("speed_sust_5s", "FLOAT"), ("speed_sust_10s", "FLOAT"),
              ("power_sust_6s", "FLOAT"), ("current_sust_6s", "FLOAT"),
              ("g_fast_20", "FLOAT"), ("g_fast_30", "FLOAT"), ("g_fast_40", "FLOAT"),
              ("g_lateral", "FLOAT"), ("g_brake", "FLOAT"), ("shake_index", "FLOAT"),
              ("accel_g", "FLOAT"), ("brake_g", "FLOAT"),
              ("t_0_60_s", "FLOAT"), ("t_0_100_s", "FLOAT"),
              ("accel_g_30", "FLOAT"), ("accel_g_50", "FLOAT"),
              ("brake_g_30", "FLOAT"), ("brake_g_50", "FLOAT"),
              ("stop_30_s", "FLOAT"), ("stop_50_s", "FLOAT"), ("moving_s", "FLOAT"),
              ("cutout_count", "INTEGER"), ("descent_m", "FLOAT"), ("lift_count", "INTEGER"), ("spin_count", "INTEGER"), ("clan_id", "VARCHAR")],
    "riders": [("public_id", "VARCHAR")],
    "clans": [("terr_km2", "FLOAT"), ("terr_best_km2", "FLOAT"),
              ("terr_tiles", "INTEGER"), ("terr_regions", "INTEGER"),
              ("targets_json", "TEXT"),
              ("terr_best_tiles", "INTEGER"), ("terr_best_fresh", "INTEGER"),
              ("terr_rank", "INTEGER"), ("terr_prev_rank", "INTEGER"),
              ("invite_key", "VARCHAR")],
    "wheels": [("alt_keys", "TEXT"), ("ble_mac", "VARCHAR"), ("serial", "VARCHAR")],
    "rider_stats": [("best_freespin", "FLOAT"), ("best_voltage_sag", "FLOAT"),
                    ("best_sustained_accel", "FLOAT"), ("total_moving_s", "FLOAT"),
                    ("real_ride_count", "INTEGER")],
}


def backfill_public_ids(db_path: str | None = None) -> int:
    """Give every rider an opaque public id. Idempotent; runs at startup.

    Separate from ensure_schema because adding the column is not enough: until every row has
    a value the public API has nothing to publish in place of the store_id, and a half-filled
    column would mean some riders silently vanish from the leaderboards.
    """
    import secrets
    import sqlite3
    path = db_path or str(config.DB_PATH)
    con = sqlite3.connect(path)
    n = 0
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(riders)")}
        if "public_id" not in cols:
            return 0
        # Not only the empty ones. A malformed-but-present handle survived every restart and
        # waited for whichever route happened to read it first, which is how a polluted row
        # stayed polluted while four different call sites each decided what to do about it.
        # GLOB is case-sensitive in SQLite, so uppercase hex is repaired too.
        hexglob = "[0-9a-f]" * 16
        rows = con.execute(
            "SELECT store_id FROM riders WHERE public_id IS NULL OR public_id = ''"
            f" OR public_id NOT GLOB '{hexglob}'").fetchall()
        for (sid,) in rows:
            con.execute("UPDATE riders SET public_id=? WHERE store_id=?",
                        (secrets.token_hex(8), sid))
            n += 1
        con.commit()
    finally:
        con.close()
    return n


# Indexes the ORM does not declare, added idempotently alongside the columns. `trips.clan_id`
# is the one that matters: the contributors query filters on it and without an index SQLite
# walked every validated trip, which gets slower every week the site stays up.
NEW_INDEXES = [
    ("ix_trips_clan_start", "trips", "(clan_id, start_utc)"),
    ("ix_clans_terr_best", "clans", "(terr_best_km2)"),
]


def ensure_indexes(db_path: str | None = None) -> list[str]:
    import sqlite3
    path = db_path or str(config.DB_PATH)
    con = sqlite3.connect(path)
    made = []
    try:
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for name, table, cols in NEW_INDEXES:
            if name in have or table not in tables:
                continue
            con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")
            made.append(name)
        con.commit()
    finally:
        con.close()
    return made


def ensure_schema(db_path: str | None = None) -> list[str]:
    """Idempotently add any missing columns to an existing SQLite file.
    Returns the list of columns added (empty when already up to date)."""
    import sqlite3
    path = db_path or str(config.DB_PATH)
    added = []
    con = sqlite3.connect(path)
    try:
        for table, cols in NEW_COLUMNS.items():
            existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue                      # table doesn't exist yet (fresh file)
            for name, typ in cols:
                if name not in existing:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
                    added.append(f"{table}.{name}")
        con.commit()
    finally:
        con.close()
    return added


def init_db():
    """Create all tables. Importing `models` registers them on Base.metadata."""
    try:
        import models  # noqa: F401
    except ImportError:
        pass
    Base.metadata.create_all(bind=engine)
    ensure_schema()                           # backfill columns on pre-existing DBs
    backfill_public_ids()                     # and give every rider an opaque public handle
    ensure_indexes()
