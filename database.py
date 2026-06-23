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
              ("cutout_count", "INTEGER"), ("descent_m", "FLOAT")],
    "rider_stats": [("best_freespin", "FLOAT"), ("best_voltage_sag", "FLOAT"),
                    ("best_sustained_accel", "FLOAT"), ("total_moving_s", "FLOAT"),
                    ("real_ride_count", "INTEGER")],
}


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
