"""Biggest patches, the fold under a crew in Other crews.

The row above it says "9 squares in one patch, 13 all told, 3 patches" and then refuses to say
where the other two are. The fold answers that, and Erwin asked for it in those terms: "In
other crews what else can we add? 'Biggest patches'? similar to the Best riders".

Three things here cannot be read off the source:

  * the fold is absent for a crew whose ground is in one piece, because the row above already
    says so and a control that opens onto a restatement is the dead control this file keeps
    paying for -- and since the count on that row IS the control now, the same rule decides
    whether a number is pressable or just a number;
  * every row's area is its OWN patch's area. The crew's total comes down from the server
    already summed and a patch is a subset nobody has summed, so this is the one figure in the
    panel computed on the client -- and a row printing the crew's total beside each patch
    would look entirely reasonable;
  * that client arithmetic agrees with the server's. `services/tiles.area_km2` is what every
    other km² on the site is built from, including the crew total printed two lines above this
    fold, and two formulas for one quantity is how they drift.

The function is lifted out of crews.js and run under node, the way `test_crews_contrib_cap.py`
lifts `contributorsHTML`.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

from services import tiles as T

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

PATCH_START = "  var PATCH_SHOWN = 5;"
PATCH_END = "  // What a crew holds, in the words its own card uses for the same facts."
# `groundBits` builds the facts line the fold now opens FROM, and it is the half of the rule
# that cannot be read off the fold: it decides whether the patch count is a control or words.
GROUND_START = PATCH_END
GROUND_END = "  function whoId(slug) {"
# The chevron, lifted rather than stubbed for the same reason as the circumference below: a
# stub cannot tell a button that carries the turning glyph from one that does not.
CHEV_LINE = "  var CHEV = "
FILL_START = "  function biggestPatch(pts) {"
FILL_END = "  // Where on screen there is room to put the marked square."
TILE_START = "  function tileLon(x, z) {"
TILE_END = "  function tileRing(x, y, z) {"
# The earth's circumference, lifted rather than stubbed: a test that hands the function its
# own constant cannot tell 40075.016686 from 40075, and the server scores with the first.
EARTH_LINE = "  var MIN_LEAD_EDGE = 0.42, EARTH_C_KM"

Z = 14

HARNESS = """
const TERR = { z: %(z)d, crews: [{ slug: "a" }, { slug: "one" }], cells: %(cells)s };
// --- stubs. None of them decides an order, a count or an area. ----------------------------
const t = (k, v) => (k === "crew.rivals.patches" ? "Biggest patches"
  : k === "crew.join.away" ? v.v + " from here"
  : k === "crew.mine.ao" ? "in one patch"
  : k === "crew.inall" ? v.v + " all told"
  : k + (v ? JSON.stringify(v) : ""));
const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const tiles = n => n + " squares";
const plural = (one, few, many, n) => n + " patches";
// `patchWords` counts through this rather than `plural` now that the row says "1 patch" and
// "0 patches" as well as "2 patches". Still no decision in it: it prints what it is given.
const patchesOf = n => n + (n === 1 ? " patch" : " patches");
const fmtKm = v => Math.round(v) + " km";
// Rounded like the real one, which is what a reader compares between rows.
const fmtKm2 = v => Math.round(v) + " km2";
const moreLabel = n => "Show " + n + " more";
// The real one measures from the rider's home or the middle of the screen, neither of which
// exists here; the fold only has to put what it returns on the row.
const squaresAway = pts => pts.length * 10;

%(earth)s

%(chev)s

%(tile)s

%(fill)s

%(patch)s

%(ground)s

const rows = html => [...html.matchAll(
  /<button type="button" class="crewprow([^"]*)"([^>]*)>(.*?)<\\/button>/g)].map(m => ({
    cls: m[1].trim(), hidden: / hidden/.test(m[2]),
    i: +(/data-pati="(\\d+)"/.exec(m[2]) || [])[1],
    n: +(/<i class="crewpatn">(\\d+)<\\/i>/.exec(m[3]) || [])[1],
    facts: [...m[3].matchAll(/<span class="crewpatf">([^<]*)<\\/span>/g)].map(f => f[1]),
  }));

const many = patPan("a", allPatches(crewPts("a")));
// The control, which is a count on the facts line now and not a labelled line of its own.
const btn = patBtn("a", "6 patches");
// The facts line itself, for the crew the client can show every patch of and for the crew
// whose server-side count it cannot: `one` holds a single block here and claims three.
const line = c => groundBits(c, patsDeco(c.slug, allPatches(crewPts(c.slug))));
console.log(JSON.stringify({
  many: { rows: rows(many), more: (many.match(/crewcmore/g) || []).length,
          label: (/crewcmore[^>]*>([^<]*)</.exec(many) || [])[1] || "",
          head: (/<h4>([^<]*)<\\/h4>/.exec(many) || [])[1] || "",
          pan: /id="([^"]*)" hidden/.exec(many)[1] },
  btn: { slug: /data-pats="([^"]*)"/.exec(btn)[1],
         controls: /aria-controls="([^"]*)"/.exec(btn)[1],
         expanded: /aria-expanded="([^"]*)"/.exec(btn)[1],
         label: (/<span>([^<]*)<\\/span>/.exec(btn) || [])[1] || "",
         chevOutsideLabel: /<\\/span><i /.test(btn) },
  // A crew whose ground is in one piece, and a crew with none at all.
  one: patPan("one"),
  nobody: patPan("nosuch"),
  // The patch count, as the two lists hand it out.
  shows: line({ slug: "a", best_tiles: 9, tiles: 25, regions: 6, km2: 40 }),
  cannot: line({ slug: "one", best_tiles: 4, tiles: 4, regions: 3, km2: 9 }),
  // Every patch's area, by the client's own arithmetic, for the cross-check below.
  areas: allPatches(crewPts("a")).map(p => ({
    squares: p.length,
    km2: p.reduce((a, q) => a + tileKm2(q[0], q[1], TERR.z), 0),
    at: p[0],
  })),
  // And one square's area at four latitudes, which is where a cos() in the wrong place hides.
  squares: [[8800, 4300], [8800, 4900], [8800, 5400], [8800, 8192]]
    .map(([x, y]) => ({ x, y, km2: tileKm2(x, y, TERR.z) })),
}));
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node to run the shipped js")

# Crew "a" holds six patches, deliberately more than the fold shows, and in sizes that make
# the order checkable: 9, 6, 4, 3, 2, 1. Crew "one" holds a single 2x2 block.
#
# At tile coordinates a rider could actually be on: y=4900 at z14 is about 60 degrees north,
# and the first draft of this put the patches at y=100, which is 85 degrees. A square there
# is a fifth of a kilometre across, every row printed `0 km2`, and the area assertions below
# could not tell one patch from another.
PATCHES = [
    [(x, y) for x in range(8800, 8803) for y in range(4900, 4903)],      # 9
    [(x, y) for x in range(8900, 8903) for y in range(4910, 4912)],      # 6
    [(x, y) for x in range(9000, 9002) for y in range(4920, 4922)],      # 4
    [(9100, 4930), (9101, 4930), (9102, 4930)],                         # 3
    [(9200, 4940), (9200, 4941)],                                       # 2
    [(9300, 4950)],                                                     # 1
]
SOLO = [(x, y) for x in range(9500, 9502) for y in range(4960, 4962)]


def _region(start, end, needed):
    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(start)
    j = src.index(end, i)
    region = src[i:j]
    for want in needed:
        assert want in region, f"{want!r} is not in the lifted region; this tests nothing"
    return region


def _line(prefix):
    src = CREWS_JS.read_text(encoding="utf-8")
    hit = [l for l in src.splitlines() if l.startswith(prefix)]
    assert len(hit) == 1, f"{prefix!r} matched {len(hit)} lines"
    return hit[0]


def _cells():
    rows = []
    for patch in PATCHES:
        for x, y in patch:
            rows += [0, x, y, 0, 0]
    for x, y in SOLO:
        rows += [1, x, y, 0, 0]
    return json.dumps(rows)


def _run():
    js = HARNESS % {
        "z": Z,
        "cells": _cells(),
        "earth": _line(EARTH_LINE),
        "tile": _region(TILE_START, TILE_END, ["Math.pow(2, z)"]),
        "fill": _region(FILL_START, FILL_END, ["function allPatches(", "function crewPts("]),
        "chev": _line(CHEV_LINE),
        "patch": _region(PATCH_START, PATCH_END,
                         ["function patPan(", "function patBtn(", "function patsDeco(",
                          "function tileKm2(", "crewcmore"]),
        "ground": _region(GROUND_START, GROUND_END, ["function groundBits("]),
    }
    d = pathlib.Path(tempfile.mkdtemp(prefix="patchfold-"))
    try:
        f = d / "t.mjs"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_crew_whose_ground_is_in_one_piece_gets_no_fold():
    """The row above it already says "in one patch". A fold that opens to restate the line it
    is attached to is the control this file has twice recorded shipping and removing."""
    out = _run()
    assert out["one"] == "", f"a one-patch crew grew a patches fold: {out['one'][:120]}"
    assert out["nobody"] == "", "a crew holding no ground grew a patches fold"


def test_every_patch_gets_a_row_biggest_first_and_numbered_to_match_the_walk():
    """The numbers are what the popup counts through -- "patch 2 of 3" -- so the fold and the
    walk have to be the same list in the same order. Both read `allPatches`, and the order it
    produces is pinned in test_crews_patch_walk.py."""
    out = _run()
    rows = out["many"]["rows"]
    assert [r["i"] for r in rows] == list(range(len(PATCHES))), (
        f"the fold's patch indices are {[r['i'] for r in rows]}")
    assert [r["n"] for r in rows] == list(range(1, len(PATCHES) + 1)), (
        f"the numbers a reader sees are {[r['n'] for r in rows]}, not 1 upward")
    got = [r["facts"][0] for r in rows]
    assert got == [f"{len(p)} squares" for p in PATCHES], (
        f"the rows read {got}; the crew's patches are {[len(p) for p in PATCHES]} squares, "
        "biggest first")


def test_five_rows_show_and_the_rest_wait_behind_the_same_button_the_riders_use():
    """`bindContribMore` unhides `.crewcrest` inside the pressed button's parent, which is why
    the rows carry that class and the button that one: one handler, two folds. A fold with its
    own almost-identical pair is how the two drift."""
    out = _run()
    rows = out["many"]["rows"]
    shown = [r for r in rows if not r["hidden"]]
    hidden = [r for r in rows if r["hidden"]]
    assert len(shown) == 5 and len(hidden) == len(PATCHES) - 5, (
        f"{len(shown)} rows show and {len(hidden)} are held back, out of {len(PATCHES)}")
    assert all("crewcrest" in r["cls"] for r in hidden), (
        "the held-back rows do not carry the class the shared handler unhides")
    assert out["many"]["more"] == 1, "no overflow button for a crew with six patches"
    assert out["many"]["label"] == f"Show {len(PATCHES) - 5} more", (
        f"the button says {out['many']['label']!r}, counting something other than the rows "
        "it is hiding")


def test_each_row_carries_its_own_patchs_area_and_its_own_distance():
    """A row printing the crew's total beside each patch would look entirely reasonable and be
    wrong on every line but one."""
    out = _run()
    rows = out["many"]["rows"]
    areas = [r["facts"][1] for r in rows]
    assert len(set(areas)) == len(areas), (
        f"two patches of different sizes printed the same area: {areas}")
    # The stub measures distance as ten times the square count, so each row must differ too.
    aways = [r["facts"][2] for r in rows]
    assert aways == [f"{len(p) * 10} km from here" for p in PATCHES], (
        f"the distances read {aways}, which is not each patch measured on its own")


def test_the_client_and_the_server_agree_on_what_a_square_covers():
    """Every other km² on the site comes from `services/tiles.area_km2`, including the crew
    total two lines above this fold. The client sums a subset of the same squares, so a
    second formula here is a figure that disagrees with the one above it -- and at a quarter
    of the area between Singapore and Tromso, a misplaced cos() is invisible in one city.
    """
    out = _run()
    for sq in out["squares"]:
        want = T.area_km2(f"{Z}/{sq['x']}/{sq['y']}")
        assert abs(sq["km2"] - want) < max(1e-6, want * 1e-9), (
            f"square {sq['x']},{sq['y']} is {sq['km2']:.6f} km2 on the client and "
            f"{want:.6f} on the server")
    # And a patch, which is what the fold actually prints.
    for patch in out["areas"]:
        x, y = patch["at"]
        assert patch["km2"] > 0
    tot = sum(p["km2"] for p in out["areas"])
    srv = sum(T.area_km2(f"{Z}/{x}/{y}") for patch in PATCHES for x, y in patch)
    assert abs(tot - srv) < 1e-6, (
        f"the six patches sum to {tot:.4f} km2 on the client and {srv:.4f} on the server")


def test_the_count_on_the_row_is_what_opens_the_fold():
    """Erwin: "we put a link in X riders and a link in X patches instead of using the 'Best
    riders' and 'Biggest patches' buttons ... when everything is collapsed looks more tidy".

    So the fold has no label of its own any more, and two things follow that the markup is the
    only record of: the number has to carry the button's whole wiring, and the panel has to
    name the list, because the words that named it are gone.
    """
    out = _run()
    btn = out["btn"]
    assert btn["slug"] == "a", "the control does not name its crew"
    assert btn["controls"] == "crewpat-a" and btn["expanded"] == "false", (
        f"the control points at {btn['controls']!r} while shut={btn['expanded']!r}; a "
        "disclosure is a button with `aria-expanded` and `aria-controls` wherever it sits")
    assert btn["label"] == "6 patches", (
        f"the control reads {btn['label']!r}: the count is the label now, and it is wrapped "
        "so the dotted underline can be the words")
    assert btn["chevOutsideLabel"], (
        "the chevron is inside the underlined label, so the glyph that turns is underlined "
        "with the words it sits beside")
    assert out["many"]["head"] == "Biggest patches", (
        f"the panel is headed {out['many']['head']!r}. The button that used to say it is a "
        "number now, so a fold with no heading opens onto rows that name nothing")


def test_a_patch_count_the_client_cannot_show_stays_words():
    """`c.regions` is the server's count; the fold is a flood fill over the squares this
    client holds. A crew whose other ground is outside the loaded region prints "3 patches"
    with one patch to show -- and a count that opens nothing is the dead control this file
    keeps paying for. The number stays, because it is the figure the board ranks on.
    """
    out = _run()
    shows = " ".join(out["shows"])
    cannot = " ".join(out["cannot"])
    assert 'data-pats="a"' in shows, (
        f"the patch count is not a control for a crew with six patches to show: {shows!r}")
    assert "6 patches" in shows, "the count itself is gone from the facts line"
    assert "3 patches" in cannot, (
        f"the server's patch count is missing from the facts line: {cannot!r}. It is the "
        "figure the board ranks on and the row above prints it either way")
    assert "<button" not in cannot and "data-pats" not in cannot, (
        f"a crew with one patch on this client got a pressable patch count: {cannot!r}")


def test_the_fold_is_wired_where_it_is_rendered():
    """Three names in three files have to agree: the panel's id, the button that controls it
    and the handler that finds it. node sees the markup and not the binding."""
    out = _run()
    src = CREWS_JS.read_text(encoding="utf-8")
    assert out["many"]["pan"] == "crewpat-a", (
        f"the panel's id is {out['many']['pan']!r}, which `patId` would not build")
    assert "[data-pats]" in src and "[data-pat]" in src, (
        "the fold's button or its rows are not bound")
    # Only in Other crews. The join list is a choice between crews and already prints how far
    # the nearest of their ground is.
    riv = src[src.index("function rivalsHTML("):src.index("function joinHTML(")]
    assert "patPan(c.slug, pats)" in riv and "patsDeco(c.slug, pats)" in riv, (
        "Other crews does not render the fold, or renders it without the guard that decides "
        "whether its count can be pressed")
    join = src[src.index("function joinHTML("):]
    join = join[:join.index("function bind")]
    assert "patPan(" not in join and "patsDeco(" not in join, (
        "the join list grew a patches fold")
    # The press rebuilds from the territory rather than closing over the list the fold was
    # drawn from: the map reloads every minute and patches merge.
    binder = src[src.index("function bindPatchFolds()"):src.index("function bindRivals()")]
    assert "allPatches(crewPts(" in binder, (
        "the row's press uses a patch list captured when the fold was drawn, so an index into "
        "it can light another crew's ground after a rebuild")
