"""Walking a crew's patches from the map popup, one press at a time.

A crew with ground in three places had one reachable patch: the popup's Highlight framed the
square under your finger, and the only way to the other two was to hunt the map for a square
of the right colour. Erwin asked for the walk instead -- "'Next patch' as the other option (IF
available) and zoom and highlight the next patch to browse patches" -- and a walk has three
things a test can pin and a reader cannot:

  * it reaches every patch and comes back, rather than bouncing between two;
  * it goes in the same order on every reload, because the fold under Other crews numbers the
    same patches and the two must agree;
  * the popup comes back on their ground and AFTER the light goes out, which is the bug this
    popup already has a fix recorded for -- it is 188x161 on a phone and the thing it would
    cover is the patch that has just lit.

`hopPatch` is lifted out of crews.js and run under node against stubs that record what it
asked for, the way `test_crews_highlight_scope.py` lifts `patchFeatures`: the order comes out
of two sorts and a flood fill, and no static read of this file can tell a cycle of three from
a cycle of two.
"""
import json
import pathlib
import random
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

# Four regions: the coordinate maths, the three flood fills and their readers, and the hop
# itself with the two helpers it places the popup by.
TILE_START = "  function tileLon(x, z) {"
TILE_END = "  function tileRing(x, y, z) {"
FILL_START = "  function biggestPatch(pts) {"
FILL_END = "  // Where on screen there is room to put the marked square."
HOP_START = "  var hopTok = 0;"
HOP_END = "  // The framing for Highlight."

# Three patches, on purpose: two of them cannot tell "next" from "the other one", and the
# wrap only has somewhere to go wrong with a third.
#
#   one square at 50,50    -- the smallest, and the furthest north-west
#   a 5x5 block at 100,100 -- twenty-five squares, and odd-sided so it has a middle square
#   a 2x2 at 200,200       -- four
#
# The lone square is north-west of the others deliberately. With the patches laid out in size
# order on the grid, dropping the sort on size entirely leaves the order unchanged and the
# fixture agrees with a fill that does not sort at all -- which is the shape of the mistake
# this file is here to catch, and the first version of it passed with the sort removed.
LONE = [(50, 50)]
BIG = [(x, y) for x in range(100, 105) for y in range(100, 105)]
MID = [(x, y) for x in range(200, 202) for y in range(200, 202)]
# A patch whose centroid falls outside itself, which is where "the middle of a patch" and "a
# square in the middle of a patch" stop being the same thing.
BENT = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2)]

HARNESS = """
const TERR = { z: 14, crews: [{ slug: "a" }], cells: %(cells)s };
const BLINK_MS = 1420;
// What the hop asks the rest of the file for, recorded rather than done.
let POPUP = null;
const log = { blink: [], reopen: [], timers: [] };
const blink = (slug, at) => log.blink.push([slug, at[0], at[1]]);
const openCrewPop = (ll, p) => log.reopen.push([ll.lng, ll.lat, p.slug]);
const setTimeout = (fn, ms) => log.timers.push([ms, fn]);

%(tile)s

%(fill)s

%(hop)s

// --- the walk. One press per patch, plus one more to prove it comes back. -----------------
const crew = { slug: "a", name: "A" };
const patches = allPatches(crewPts("a"));
const sizes = patches.map(p => p.length);
const firsts = patches.map(p => [p[0][0], p[0][1]]);

// Where a press from patch `pi` lands: the square it blinks, and the patch that square is in.
function press(pi) {
  log.blink = []; log.reopen = []; log.timers = [];
  POPUP = null;
  hopPatch(crew, patches, pi);
  // The reopen is scheduled, not immediate; run it the way the browser would.
  const fired = log.timers.map(([ms, fn]) => { fn(); return ms; });
  const at = log.blink[0];
  const lit = patchIndexAt(patches, [at[1], at[2]]);
  // Where the popup SHOULD have come back, so the walk checks the coordinate it was actually
  // given rather than trusting `middleSquare` to be called.
  const want = squareCentre(middleSquare(patches[lit]));
  const got = log.reopen[0];
  return { lit, delay: fired[0], reopened: log.reopen.length, where: got,
           onMiddle: !!got && Math.abs(got[0] - want.lng) < 1e-9
                            && Math.abs(got[1] - want.lat) < 1e-9 };
}

const walk = [];
let at = 0;
for (let i = 0; i < patches.length + 1; i++) { const r = press(at); walk.push(r); at = r.lit; }

// A press from a square the crew does not hold, which `patchIndexAt` answers -1 for.
const fromNowhere = press(-1);

// A press while the first blink is still running: the stale timer must not raise a popup
// over the patch the second press is lighting.
log.blink = []; log.reopen = []; log.timers = [];
POPUP = null;
hopPatch(crew, patches, 0);
const first = log.timers.slice();
hopPatch(crew, patches, 1);          // pressed again
const second = log.timers.slice(first.length);
first.forEach(([, fn]) => fn());     // the first press's timer fires late
const staleReopens = log.reopen.length;
second.forEach(([, fn]) => fn());

// Same walk, with the cells in another order. The server is free to emit them differently.
const TERR2 = { z: 14, crews: [{ slug: "a" }], cells: %(shuffled)s };
const shuffled = (() => {
  const keep = TERR.cells;
  TERR.cells = TERR2.cells;
  const out = allPatches(crewPts("a"));
  TERR.cells = keep;
  return { sizes: out.map(p => p.length), firsts: out.map(p => [p[0][0], p[0][1]]) };
})();

console.log(JSON.stringify({
  sizes, firsts, walk, fromNowhere, staleReopens, lateReopens: log.reopen.length, shuffled,
  // The middle of a patch, which is where the popup is put back.
  middleBig: middleSquare(%(big)s),
  middleBent: middleSquare(%(bent)s),
  centre: squareCentre([100, 100]),
  corners: [tileLon(100, 14), tileLat(100, 14), tileLon(101, 14), tileLat(101, 14)],
}));
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node to run the shipped js")


def _region(start, end, needed):
    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(start)
    j = src.index(end, i)
    region = src[i:j]
    for want in needed:
        assert want in region, f"{want!r} is not in the lifted region; this tests nothing"
    return region


def _cells(order=None):
    pts = list(BIG + MID + LONE)
    if order is not None:
        order.shuffle(pts)
    rows = []
    for x, y in pts:
        rows += [0, x, y, 0, 0]
    return json.dumps(rows)


def _run():
    js = HARNESS % {
        "cells": _cells(),
        "shuffled": _cells(random.Random(7)),
        "tile": _region(TILE_START, TILE_END, ["Math.pow(2, z)"]),
        "fill": _region(FILL_START, FILL_END,
                        ["function allPatches(", "function patchIndexAt(", "function crewPts("]),
        "hop": _region(HOP_START, HOP_END,
                       ["function hopPatch(", "function middleSquare(", "hopTok"]),
        "big": json.dumps([list(q) for q in BIG]),
        "bent": json.dumps([list(q) for q in BENT]),
    }
    d = pathlib.Path(tempfile.mkdtemp(prefix="patchwalk-"))
    try:
        f = d / "t.mjs"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_every_patch_is_found_and_the_biggest_leads():
    """`biggestPatch` is this with the tail thrown away, so a fill that stops at the first
    component is the failure that looks like working software: one patch, no walk."""
    out = _run()
    assert out["sizes"] == [len(BIG), len(MID), len(LONE)], (
        f"the crew's three patches came back as {out['sizes']}, and it holds "
        f"{len(BIG)}, {len(MID)} and {len(LONE)} squares in three places")


def test_the_order_is_the_same_whichever_order_the_squares_arrive_in():
    """The fold under Other crews numbers these patches and the popup counts "patch 2 of 3"
    through them. A flood fill's own order follows the cell array, which the server rebuilds
    nightly and is free to emit differently -- so without the sort, patch 2 is a different
    patch tomorrow and the two surfaces disagree about which."""
    out = _run()
    assert out["shuffled"]["sizes"] == out["sizes"], (
        "shuffling the cells changed which patch is biggest")
    assert out["shuffled"]["firsts"] == out["firsts"], (
        f"shuffling the cells changed where each patch starts: {out['firsts']} became "
        f"{out['shuffled']['firsts']}, so the numbering is not stable across a rebuild")


def test_next_patch_reaches_every_patch_and_comes_back():
    """Three patches, three presses, then the fourth returns to where it started. A walk that
    bounces between the first two is what a modulo off by one gets you, and on a crew with
    two patches it is indistinguishable from working."""
    out = _run()
    lit = [step["lit"] for step in out["walk"]]
    assert lit == [1, 2, 0, 1], (
        f"walking from the biggest patch visited {lit}, which is not every patch and back")


def test_a_press_from_ground_the_crew_does_not_hold_starts_at_the_biggest_patch():
    """`patchIndexAt` answers -1 for a square the crew does not hold, which is reachable: the
    popup can be raised from an emblem's own coordinate, and a rebuild can move a border under
    an open one. `-1 + 1` has to be the biggest patch and not patch two."""
    out = _run()
    assert out["fromNowhere"]["lit"] == 0, (
        f"a press from a square the crew does not hold went to patch "
        f"{out['fromNowhere']['lit'] + 1} of {len(out['sizes'])} rather than the biggest")
    assert out["fromNowhere"]["reopened"] == 1, (
        "the popup did not come back from a press that had nowhere to start")


def test_the_popup_comes_back_on_their_ground_and_in_the_middle_of_it():
    """A patch is not always convex -- an L of squares has a centroid outside itself -- so the
    popup is put on the square nearest the middle rather than at the middle. Anchored at a
    point the crew does not hold, the popup would name a crew and point somewhere else."""
    out = _run()
    assert out["middleBig"] == [102, 102], (
        f"the middle of a 5x5 came back as {out['middleBig']}, not its centre square")
    assert tuple(out["middleBent"]) in {tuple(q) for q in BENT}, (
        f"the middle of a bent patch is {out['middleBent']}, which is not one of its squares")
    w, n, e, s = out["corners"]
    lng, lat = out["centre"]["lng"], out["centre"]["lat"]
    assert w < lng < e and s < lat < n, (
        f"the popup's coordinate {lng},{lat} is not inside the square it belongs to "
        f"({w}..{e}, {s}..{n})")
    # And that the walk actually anchors there. Asserting `middleSquare` alone leaves the
    # hop free to anchor anywhere: a version of this that passed every other test in the
    # file put the popup on the patch's north-west corner square.
    for i, step in enumerate(out["walk"] + [out["fromNowhere"]]):
        assert step["onMiddle"], (
            f"press {i + 1} raised the popup at {step['where']}, which is not the middle of "
            f"patch {step['lit'] + 1}")


def test_every_press_frames_the_patch_it_is_walking_to():
    """`blink` is handed a square INSIDE the next patch, which is what makes `patchFeatures`
    frame that patch rather than the crew's biggest. Handed the wrong square the camera flies
    to Oslo while the counter says patch 2 of 3."""
    out = _run()
    for i, step in enumerate(out["walk"]):
        assert step["lit"] == [1, 2, 0, 1][i], "the walk changed under this test"
        assert step["reopened"] == 1, (
            f"press {i + 1} raised the popup {step['reopened']} times")


def test_the_popup_waits_for_the_light_to_go_out():
    """It is 188x161 on a phone and parked on the ground it has just lit. This file records
    fixing that once already, by having Highlight dismiss the popup before lighting anything;
    bringing it back 700ms later re-creates the same bug with extra steps."""
    out = _run()
    for step in out["walk"]:
        assert step["delay"] > 1420, (
            f"the popup is raised {step['delay']}ms in, while the blink is still flashing")


def test_pressing_again_cancels_the_press_that_was_still_running():
    """Two presses inside a second and a half is one thumb on one button, which is how this
    control is meant to be used. The first press's timer must not raise a popup over the patch
    the second one is lighting."""
    out = _run()
    assert out["staleReopens"] == 0, (
        "a stale press raised the popup after the reader had already moved on")
    assert out["lateReopens"] == 1, (
        f"the press that was actually last raised the popup {out['lateReopens']} times")
