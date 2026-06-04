"""Tiny key/value settings stored in the active dataset's app_meta table.

Because app_meta lives *inside* the SQLite file, per-dataset flags such as
``is_test`` travel automatically when the dataset is swapped. Values are stored
as strings; helpers coerce the few typed ones we use.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import Meta

IS_TEST = "is_test"


def get_meta(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(Meta, key)
    return row.value if row else default


def set_meta(db: Session, key: str, value: str) -> None:
    row = db.get(Meta, key)
    if row:
        row.value = str(value)
    else:
        db.add(Meta(key=key, value=str(value)))
    db.commit()


def is_test_dataset(db: Session) -> bool:
    """Whether the active dataset is test data. Defaults to True when unset so
    a dataset of unknown provenance shows the TEST DATA banner (fail safe)."""
    return get_meta(db, IS_TEST, "1") not in ("0", "false", "False", "")


def set_test(db: Session, value: bool) -> None:
    set_meta(db, IS_TEST, "1" if value else "0")
