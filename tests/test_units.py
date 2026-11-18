"""Unit defaults must follow where a rider IS, not the language their browser is set to.

The Denmark bug: `defaultUnit()` read the region subtag of `navigator.language`, so a
Danish rider reading the site in English (`en-GB` / `en-US`) was served miles, Fahrenheit
and feet. Language is a reading preference; it says nothing about which units someone uses.
"""
import json
import re

from fastapi.testclient import TestClient

from main import app

METRIC = {"d": 0, "t": 0, "a": 0}
IMPERIAL = {"d": 1, "t": 1, "a": 1}
ROAD_ONLY = {"d": 1, "t": 0, "a": 0}      # miles & mph, but Celsius and metres


def _page() -> str:
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    return r.text


def _table(page: str, name: str) -> dict:
    m = re.search(name + r"\s*=\s*(\{.*?\})\s*;", page, re.S)
    assert m, f"{name} lookup table not found in the served page"
    return json.loads(m.group(1))


def _profile_for(page: str, tz: str) -> dict:
    """Resolve a timezone to its unit profile the same way the client does."""
    region = _table(page, "IMPERIAL_TZ").get(tz, "")
    profile = _table(page, "REGION_PROFILE").get(region, "kmh")
    return _table(page, "UNIT_PROFILES")[profile]


def test_units_are_not_chosen_from_browser_language():
    page = _page()
    assert "MPH_REGIONS" not in page, "the language-region lookup is back"
    m = re.search(r"function defaultUnit\(\)\{.*?\n", page)
    assert m, "defaultUnit() not found"
    assert "navigator.language" not in m.group(0), "unit default still reads the UI language"


def test_metric_countries_get_metric():
    page = _page()
    for tz in ("Europe/Copenhagen", "Europe/Oslo", "Europe/Berlin", "Europe/Madrid",
               "America/Toronto", "America/Mexico_City", "Asia/Tokyo", "Australia/Sydney"):
        assert _profile_for(page, tz) == METRIC, tz


def test_united_states_gets_full_imperial():
    page = _page()
    for tz in ("America/New_York", "America/Chicago", "America/Denver",
               "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu"):
        assert _profile_for(page, tz) == IMPERIAL, tz


def test_uk_and_myanmar_get_miles_but_keep_celsius_and_metres():
    page = _page()
    assert _profile_for(page, "Europe/London") == ROAD_ONLY
    assert _profile_for(page, "Asia/Yangon") == ROAD_ONLY


def test_liberia_gets_full_imperial():
    assert _profile_for(_page(), "Africa/Monrovia") == IMPERIAL


def test_unknown_timezone_falls_back_to_metric():
    """Never guess imperial: an unrecognised or missing zone must stay metric."""
    assert _profile_for(_page(), "Not/AZone") == METRIC


def test_us_territories_and_crown_dependencies_match_the_viewer():
    """eucviewer.ried.no resolves these zones too; the two sites must not disagree."""
    page = _page()
    for tz in ("America/Puerto_Rico", "America/St_Thomas", "Pacific/Guam", "Pacific/Pago_Pago"):
        assert _profile_for(page, tz) == IMPERIAL, tz
    for tz in ("Europe/Jersey", "Europe/Guernsey", "Europe/Isle_of_Man", "Europe/Belfast"):
        assert _profile_for(page, tz) == ROAD_ONLY, tz


def test_unit_choice_is_shared_with_the_viewer():
    """A cookie on the parent domain carries the choice between eucstats and eucviewer.

    The viewer writes `euc_units` as metric / imperial / road (its static/js/units.js); every
    one of those has to land on a profile that exists here, and the cookie has to be consulted
    before this site's own localStorage, or a change made over there would never arrive.
    """
    page = _page()
    shared = _table(page, "SHARED_UNIT")
    profiles = _table(page, "UNIT_PROFILES")
    assert set(shared) == {"metric", "imperial", "road"}
    assert all(v in profiles for v in shared.values())
    assert profiles[shared["metric"]] == METRIC
    assert profiles[shared["imperial"]] == IMPERIAL
    assert profiles[shared["road"]] == ROAD_ONLY
    assert 'let UNIT=sharedUnit()||localStorage.getItem("eucstats_unit")||defaultUnit();' in page
    assert "domain=ried.no" in page, "the cookie must be set on the parent domain to be shared"
    assert "shareUnit(UNIT)" in page, "changing units in the gear menu must update the cookie"
