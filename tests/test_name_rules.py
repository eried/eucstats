"""Display-name rules: length 3..20, cleaning, and case/space-insensitive uniqueness."""
import pytest
from fastapi.testclient import TestClient

from main import app
from services.identity import InvalidName, clean_display_name, name_taken
import models


# ---------- pure helpers ----------

def test_clean_trims_collapses_strips():
    assert clean_display_name("  Big   John  ") == "Big John"   # trim + collapse
    assert clean_display_name("Ann\n\tie") == "Annie"           # control chars stripped
    assert clean_display_name("Joe") == "Joe"                   # min ok
    assert clean_display_name("x" * 20) == "x" * 20             # max ok
    assert clean_display_name("🛞🛞🛞") == "🛞🛞🛞"             # emoji count as 1 char each


@pytest.mark.parametrize("bad", ["", "  ", "ab", "a", " x ", "\n\n"])
def test_clean_rejects_too_short(bad):
    with pytest.raises(InvalidName):
        clean_display_name(bad)


def test_clean_rejects_too_long():
    with pytest.raises(InvalidName):
        clean_display_name("x" * 21)


def test_name_taken_is_case_and_space_insensitive(db):
    db.add(models.Rider(store_id="s1", display_name="John Doe", platform="google_play"))
    db.commit()
    assert name_taken(db, "john doe") is True
    assert name_taken(db, "JOHNDOE") is True
    assert name_taken(db, "  JoHnDoE ") is True
    assert name_taken(db, "Jane") is False
    assert name_taken(db, "John Doe", exclude_store_id="s1") is False   # self doesn't count


# ---------- API: registration ----------

def _reg(client, sid, name, **extra):
    return client.post("/api/v1/riders", json={"store_id": sid, "display_name": name, **extra})


def test_register_rejects_short_and_long(db):
    with TestClient(app) as client:
        assert _reg(client, "u1", "ab").status_code == 422
        assert _reg(client, "u2", "x" * 21).status_code == 422


def test_register_rejects_duplicate_name(db):
    with TestClient(app) as client:
        assert _reg(client, "u1", "Rider One").status_code == 200
        # different person, same name (different case/spacing) -> rejected
        dup = _reg(client, "u2", "riderone")
        assert dup.status_code == 409 and "display_name_taken" in dup.text
        # a distinct name is fine
        assert _reg(client, "u3", "Rider Two").status_code == 200


def test_register_cleans_stored_name(db):
    with TestClient(app) as client:
        r = _reg(client, "u1", "  Spacey   Name  ")
        assert r.status_code == 200 and r.json()["display_name"] == "Spacey Name"


def test_reregistration_ignores_name_rules(db):
    # an existing store_id re-registering keeps its original name and is NOT re-validated
    with TestClient(app) as client:
        assert _reg(client, "u1", "Original").status_code == 200
        again = _reg(client, "u1", "x")          # too short, but ignored on re-register
        assert again.status_code == 200 and again.json()["display_name"] == "Original"


# ---------- API: edit (PATCH) ----------

def test_patch_rejects_short_and_duplicate(db):
    db.add(models.Rider(store_id="taken", display_name="Existing One", platform="google_play"))
    db.add(models.Rider(store_id="me", display_name="My Name", platform="google_play"))
    db.commit()
    with TestClient(app) as client:
        short = client.patch("/api/v1/riders/me", json={"display_name": "ab"})
        assert short.status_code == 422
        dup = client.patch("/api/v1/riders/me", json={"display_name": "existing one"})
        assert dup.status_code == 409 and "display_name_taken" in dup.text
