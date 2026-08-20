"""Two bits of panel behaviour: which tab a section opens on, and how far a country zooms.

A section always opened on its first tab, so every other board went unseen. It now opens on
a random one, chosen ONCE per browser session so it stays put while the reader is using the
page and re-rolls on the next visit.

A fly-to used one fixed zoom for every country, which lands on an anonymous patch of Texas
or Siberia and shows Denmark mostly as its neighbours. CENTROIDS already carried a per
country framing zoom as its third element; nothing read it.
"""
import re

from fastapi.testclient import TestClient

from main import app


def _page() -> str:
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    return r.text


def _centroids(page: str) -> dict:
    m = re.search(r"const CENTROIDS=\{(.*?)\};", page, re.S)
    assert m, "CENTROIDS not found"
    out = {}
    for code, body in re.findall(r"([A-Z]{2}):\[([^\]]+)\]", m.group(1)):
        out[code] = [float(x) for x in body.split(",")]
    return out


def test_every_centroid_carries_a_framing_zoom():
    for code, v in _centroids(_page()).items():
        assert len(v) == 3, f"{code} has no framing zoom"
        assert 1.0 <= v[2] <= 12.0, f"{code} zoom out of range: {v[2]}"


def test_the_fly_to_uses_the_country_zoom():
    page = _page()
    m = re.search(r"function flyToCountry\(arg\)\{.*?\n\}", page, re.S)
    assert m, "flyToCountry not found"
    assert "c[2]" in m.group(0), "the per-country zoom is still ignored"
    assert "zoom:5.8" not in m.group(0), "still hardcoding one zoom for every country"


def test_big_countries_zoom_out_further_than_small_ones():
    c = _centroids(_page())
    for big in ("US", "CA", "RU", "BR", "CN", "AU"):
        assert c[big][2] <= 4.2, f"{big} should sit well out, got {c[big][2]}"
    for small in ("DK", "BE", "CH", "NL", "SG", "LU"):
        assert c[small][2] >= 6.2, f"{small} should sit closer in, got {c[small][2]}"
    assert max(c[b][2] for b in ("US", "CA", "RU", "BR")) < \
        min(c[s][2] for s in ("DK", "BE", "CH", "NL"))


def test_the_countries_riders_actually_use_are_all_covered():
    """Whatever the live data holds must frame properly, not fall back."""
    c = _centroids(_page())
    for code in ("AT", "BE", "CA", "CN", "DK", "ID", "NO", "PL", "SE", "UA", "US"):
        assert code in c, f"{code} has no centroid, so it falls back to a generic zoom"


def test_tab_choice_is_per_session_not_per_open():
    page = _page()
    m = re.search(r"function initialTab\(sec,n\)\{.*?\n\}", page, re.S)
    assert m, "initialTab not found"
    body = m.group(0)
    assert "sessionStorage" in body, "must persist for the session, not re-roll on every open"
    assert "localStorage" not in body, "localStorage would freeze the pick forever"
    assert "Math.random" in body


def test_sections_open_on_the_chosen_tab():
    page = _page()
    assert 'initialTab("riders"' in page
    assert "initialTab(name," in page, "group sections must choose too"
    assert "vis[sel]" in page, "the chosen tab must be the one loaded"
    assert "if(vis[0])loadBoard" not in page, "still hardwired to the first tab"
