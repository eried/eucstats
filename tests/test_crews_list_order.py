"""The browse list and the board have to agree about who is ahead.

`rivalsHTML` sorts its rows with `ranked()`, whose comparator leads on `score` -- and the
payload those rows are built from, `_crew_brief`, did not carry one. So `(b.score || 0) -
(a.score || 0)` was `0 - 0` for every pair in the list and the sort fell through to
`best_tiles`, which is NOT what the board ranks on. A crew spread over two cities therefore
sorted below a compact crew it beats, while the standing printed on its own row said
otherwise: the demo world rendered 1st, 2nd, 3rd, 4th, 5th, then **7th above 6th**.

The endpoint already returns crews in the board's order -- `standing_order_by()`, with a
comment saying these two lists "used to be sorted by different rules" -- and the client then
undid it. Feeding the comparator is what makes `ranked()` reproduce that order instead of
fighting it, which matters for any list the client ever filters or merges before rendering.

This runs the SHIPPED comparator, lifted out of crews.js, over payloads built by the SHIPPED
serialiser, against the order the server's own sort key puts them in. Nothing here restates
the scoring rule; that is the point. A Python reimplementation of `byStanding` would agree
with itself forever.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
CREWS_JS = ROOT / "web" / "static" / "crews.js"

START = "  function byStanding(a, b) {"
END = "  // What a crew's score becomes if its biggest patch goes from `was` to `now`"

HARNESS = """
%(region)s

const rows = %(rows)s;
console.log(JSON.stringify(ranked(rows).map(c => c.slug)));
"""

# Compact against spread, which is the pair the two sorts disagree about:
#
#   compact  9 squares, all in one patch  -> 9*1.0 + 9*0.25  = 11.25
#   spread   8 in its biggest, 16 held    -> 8*1.0 + 16*0.25 = 12.0
#
# By score the spread crew is ahead. By biggest patch alone the compact one is, which is the
# order the list was printing.
SHAPES = [
    ("compact-crew", {"terr_best_tiles": 9, "terr_tiles": 9, "terr_regions": 1,
                      "terr_km2": 14.0, "terr_best_km2": 14.0}),
    ("spread-crew", {"terr_best_tiles": 8, "terr_tiles": 16, "terr_regions": 3,
                     "terr_km2": 26.0, "terr_best_km2": 13.0}),
]


def _clans():
    out = []
    for i, (slug, terr) in enumerate(SHAPES):
        c = types.SimpleNamespace(
            clan_id=i + 1, slug=slug, name=slug.replace("-", " ").title(),
            description="", colour="#ff0000", pattern=None, join_policy="open",
            logo_png=None, disbanded_at=None, **terr)
        out.append(c)
    return out


def _briefs(clans):
    from web.crews_api import _crew_brief
    # `counts` short-circuits the per-crew member query, so no database is touched.
    return [_crew_brief(None, c, {c.clan_id: 3}) for c in clans]


def test_the_browse_payload_carries_what_the_board_sorts_on():
    """Without this the client's comparator is reading `undefined` on its first key."""
    briefs = _briefs(_clans())
    from services.crews import standing_score
    for brief, clan in zip(briefs, _clans()):
        assert "score" in brief, (
            f"{brief['slug']} is listed without a score, so anything sorting these rows is "
            "ranking them on something the board does not use")
        assert brief["score"] == pytest.approx(standing_score(clan)), (
            f"{brief['slug']} is listed with score {brief['score']} and the board scores it "
            f"{standing_score(clan)}")
        # The comparator's third key. Two crews level on score AND biggest patch are rare and
        # the board still orders them, so a list that cannot read this one orders them by
        # whatever the browser's sort happened to do.
        assert brief["best_km2"] == pytest.approx(clan.terr_best_km2), (
            f"{brief['slug']} is listed with best_km2 {brief.get('best_km2')!r}")


def test_the_two_sorts_disagree_about_this_pair():
    """The fixture's own guard. If a weight changes and these two stop disagreeing, the test
    below passes without asking anything, and this says so instead."""
    from services.crews import standing_score
    compact, spread = _clans()
    assert standing_score(spread) > standing_score(compact), (
        "the spread crew no longer outscores the compact one")
    assert (spread.terr_best_tiles or 0) < (compact.terr_best_tiles or 0), (
        "the spread crew no longer has the smaller biggest patch, so a sort that falls "
        "through to `best_tiles` would reach the right answer by luck")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_the_list_sorts_the_crews_the_way_the_board_does():
    """The shipped comparator over the shipped payload."""
    from services.crews import standing_sort_key
    clans = _clans()
    want = [c.slug for c in sorted(clans, key=standing_sort_key)]

    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(START)
    region = src[i:src.index(END, i)]
    assert "function ranked(" in region, "the lifted region has no `ranked`"

    d = pathlib.Path(tempfile.mkdtemp(prefix="listorder-"))
    try:
        f = d / "t.mjs"
        f.write_text(HARNESS % {"region": region,
                                "rows": json.dumps(_briefs(clans))}, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        got = json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    assert got == want, (
        f"the list renders {got} and the board ranks them {want}; the standing printed on each "
        "row comes from the board, so a list in this order prints 7th above 6th")
