"""The site against the rules it shares with the EUC Planet app and the Trip Viewer.

`tests/parity_rules.json` is the agreement: which languages exist and what the picker calls
them, which code a browser language lands on, which units a rider starts in, and the two
cookies the web sites share. One rider uses all three, so a change on this side alone is a
bug even when the site still works. If a test here fails, either the change was a mistake,
or the rules file and the other two projects have to move with it.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from main import app
from web import i18n

RULES = json.loads((pathlib.Path(__file__).parent / "parity_rules.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def page() -> str:
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    return r.text


def _table(page: str, name: str) -> dict:
    m = re.search(name + r"\s*=\s*(\{.*?\})\s*;", page, re.S)
    assert m, f"{name} not found in the served page"
    return json.loads(m.group(1))


def test_same_languages_with_the_same_names():
    assert dict(sorted(i18n.LANG_NAMES.items())) == RULES["languages"]


def test_every_language_has_a_table():
    have = set(i18n.TRANSLATIONS) | {"en"}
    assert have == set(RULES["languages"])


@pytest.mark.parametrize("browser,code", RULES["language_mapping"])
def test_server_lands_a_browser_language_on_the_agreed_code(browser, code):
    assert i18n.pick(browser) == code


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_page_lands_a_browser_language_on_the_agreed_code(page, tmp_path):
    m = re.search(r"function _mapLang\(raw\)\{.*?\n  return LANGS\[base\]\?base:null;\}", page, re.S)
    assert m, "_mapLang not found in the served page"
    cases = RULES["language_mapping"]
    js = ("const LANGS=%s;\n%s\nconst cases=%s;\n"
          "console.log(JSON.stringify(cases.map(c=>_mapLang(c[0]))));\n"
          % (json.dumps({k: 1 for k in RULES["languages"]}), m.group(0), json.dumps(cases)))
    f = tmp_path / "maplang.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    wrong = [(c[0], c[1], g) for c, g in zip(cases, got) if g != c[1]]
    assert not wrong, f"(browser, agreed, page gives): {wrong}"


def test_unit_codes_are_the_agreed_eight(page):
    assert sorted(_table(page, "UNIT_PROFILES")) == sorted(RULES["units"]["codes"])


def test_starting_units_by_timezone(page):
    zones, regions = _table(page, "IMPERIAL_TZ"), _table(page, "REGION_PROFILE")
    for tz, want in RULES["units"]["default_by_timezone"].items():
        got = regions.get(zones.get(tz, ""), RULES["units"]["default"])
        assert got == want, tz


def test_older_unit_values_are_still_read(page):
    shared = _table(page, "SHARED_UNIT")
    for old, new in RULES["units"]["older_values_still_read"].items():
        assert shared.get(old) == new, old


def test_both_cookies_are_shared_on_the_parent_domain(page):
    dom = "; domain=" + RULES["cookie_domain"]
    assert page.count(dom) >= 2, "a shared cookie lost its parent domain"
    for name in (RULES["units"]["cookie"], RULES["language_cookie"]):
        assert '"%s="+' % name in page or "%s=" % name in page, name


def test_names_that_stay_in_english():
    tables = {"en": i18n.EN, **i18n.TRANSLATIONS}
    lost = []
    for name in RULES["names_kept_in_english"]:
        for key, text in i18n.EN.items():
            if name not in text:
                continue
            for loc, table in tables.items():
                if name not in table.get(key, text):
                    lost.append((loc, key, name))
    assert not lost, lost[:20]


def test_the_old_traditional_chinese_code_still_finds_its_table():
    """Traditional Chinese was `zh-Hant` here until it took the app's `zh-TW`. A page a
    browser cached before the rename still asks for the old table by name."""
    with TestClient(app) as client:
        old = client.get("/api/v1/i18n/zh-Hant", follow_redirects=False)
        assert old.status_code == 308 and old.headers["location"].endswith("/i18n/zh-TW")
        new = client.get("/api/v1/i18n/zh-TW")
    assert new.status_code == 200 and new.json()["dock.riders"]
    assert "zh-Hant" not in i18n.LANG_NAMES


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_a_stored_old_code_lands_on_the_new_one(page, tmp_path):
    fns = re.search(r"function _mapLang\(raw\)\{.*?\nfunction _knownLang\(v\)\{[^\n]*\n", page, re.S)
    assert fns, "_knownLang not found in the served page"
    js = ("const LANGS=%s;\n%s\nconsole.log(JSON.stringify(['zh-Hant','zh-TW','zh','xx',''].map(_knownLang)));\n"
          % (json.dumps({k: 1 for k in RULES["languages"]}), fns.group(0)))
    f = tmp_path / "known.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["zh-TW", "zh-TW", "zh", "", ""]
