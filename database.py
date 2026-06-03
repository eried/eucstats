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


def init_db():
    """Create all tables. Importing `models` registers them on Base.metadata."""
    try:
        import models  # noqa: F401
    except ImportError:
        pass
    Base.metadata.create_all(bind=engine)
