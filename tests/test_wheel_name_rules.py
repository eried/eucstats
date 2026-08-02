"""Wheel brand/model canonicalization (services.settings.canonicalize_name).

Real case this exists for: the app reports every Nosfet wheel as brand "LeaperKim"
with the model carrying the real maker — "NOSFET Aero", "NOSFET Apex", "NOSFET Aeon" —
so a whole marque was being credited to Leaperkim on the brand boards.

Matching used to be exact string equality, which meant one rule per model and a silent
regression every time the maker shipped a new one. A pattern may now use `*`.
"""
import pytest

from services.settings import canonicalize_name

# Exactly as prod reports them (see /api/v1/groups/wheel).
NOSFET = ["NOSFET Aero", "NOSFET Apex", "NOSFET Aeon"]
VETERAN = ["Veteran Lynx S", "Veteran Oryx", "Veteran Lynx"]

NOSFET_RULE = {"m_brand": "LeaperKim", "m_model": "NOSFET*",
               "set_brand": "Nosfet", "set_model": "", "max_app_version": ""}


@pytest.mark.parametrize("model", NOSFET)
def test_every_nosfet_model_is_rebranded_by_one_rule(model):
    brand, kept = canonicalize_name("LeaperKim", model, "0.13.1", [NOSFET_RULE])
    assert brand == "Nosfet"
    assert kept == model, "the model must be preserved, only the brand was wrong"


@pytest.mark.parametrize("model", VETERAN)
def test_genuine_leaperkim_wheels_are_untouched(model):
    assert canonicalize_name("LeaperKim", model, "0.13.1", [NOSFET_RULE]) == ("LeaperKim", model)


def test_a_future_nosfet_model_is_covered_without_a_new_rule():
    """The point of the wildcard: no silent regression when the maker ships another wheel."""
    brand, _ = canonicalize_name("LeaperKim", "NOSFET Zephyr", "0.14.0", [NOSFET_RULE])
    assert brand == "Nosfet"


def test_matching_ignores_case():
    """Apps report casing inconsistently; case never distinguishes two real wheels."""
    brand, _ = canonicalize_name("leaperkim", "Nosfet Aero", "0.13.1", [NOSFET_RULE])
    assert brand == "Nosfet"


def test_contains_pattern():
    rule = {"m_model": "*Lynx*", "set_brand": "Leaperkim", "set_model": ""}
    assert canonicalize_name("X", "Veteran Lynx S", None, [rule])[0] == "Leaperkim"
    assert canonicalize_name("X", "Veteran Oryx", None, [rule])[0] == "X"


def test_plain_patterns_stay_exact():
    """No '*' means equality — a bare word must not start matching as a substring."""
    rule = {"m_model": "Lynx", "set_brand": "WRONG", "set_model": ""}
    assert canonicalize_name("LeaperKim", "Veteran Lynx S", None, [rule])[0] == "LeaperKim"
    assert canonicalize_name("LeaperKim", "Lynx", None, [rule])[0] == "WRONG"


def test_rule_without_a_model_matcher_still_matches_on_brand_alone():
    rule = {"m_brand": "Gotway", "set_brand": "Begode", "set_model": ""}
    assert canonicalize_name("Gotway", "MSuper", None, [rule]) == ("Begode", "MSuper")


def test_trailing_serial_is_still_stripped():
    assert canonicalize_name("InMotion", "InMotion P6 (A14219B0600259A6)", None, []) \
        == ("InMotion", "InMotion P6")


# ---------- admin form -> stored rule -> rewritten wheels ----------

def _login(client):
    import json as _json
    import pyotp
    import config
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    client.get("/admin")
    secret = _json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]
    client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})


def test_admin_can_add_a_wildcard_rule_and_apply_it(db):
    """The whole path an admin actually walks: type a pattern, Apply, wheels are rebranded."""
    from fastapi.testclient import TestClient
    import models
    from main import app
    from services import settings

    db.add(models.Rider(store_id="r1", display_name="Rider One", platform="google_play"))
    for i, model in enumerate(NOSFET):
        db.add(models.Wheel(wheel_id=f"N:{i}", rider_store_id="r1", brand="LeaperKim", model=model))
    db.add(models.Wheel(wheel_id="V:0", rider_store_id="r1", brand="LeaperKim", model="Veteran Oryx"))
    db.commit()

    with TestClient(app) as client:
        _login(client)
        r = client.post("/admin/wheels/name-rule/add", data={
            "m_brand_sel": "LeaperKim", "m_model_sel": "__new__", "m_model_new": "NOSFET*",
            "set_brand_sel": "__new__", "set_brand_new": "Nosfet"}, follow_redirects=False)
        assert r.status_code == 303

        stored = settings.get_name_rules(db)
        assert stored and stored[-1]["m_model"] == "NOSFET*", "the typed pattern was not stored"

        pending = {c["wheel_id"] for c in settings.name_fix_changes(db)}
        assert pending == {"N:0", "N:1", "N:2"}, "wrong wheels queued for rename"

        assert client.post("/admin/wheels/name-apply", follow_redirects=False).status_code == 303

    db.expire_all()
    brands = {w.wheel_id: w.brand for w in db.query(models.Wheel).all()}
    assert brands == {"N:0": "Nosfet", "N:1": "Nosfet", "N:2": "Nosfet", "V:0": "LeaperKim"}
