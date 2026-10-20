"""What "Highlight squares" lights, and which patch the camera gets.

The button says *squares* and lit one patch. `patchFeatures` collected every square a crew
holds and then threw most of them away -- `biggestPatch(pts)` -- so the shine source only ever
received one connected component, and `framePatch` flew the camera to that component. Measured
on production the day this was found: Jonah's turf lit 88 of the 108 squares it holds, Arctic
Wheelers 30 of 49. Tap a square in the smaller patch and the square under your finger went
dark AND off screen, which is how a tester read it: "highlight squares is not highlighting all
the squares. I don't understand."

Two separate facts, so two separate things are checked here:

  * every square the crew holds goes into the source, because the question somebody asks of a
    square they just tapped is where ELSE these people are;
  * the patch the camera frames is the one under their finger, falling back to the crew's
    biggest when the gesture came from a list row instead of from the map.

Lifted out of crews.js and run under node, the way `test_crews_contrib_cap.py` lifts
`contributorsHTML`: the distances are computed in a loop over a flat typed-ish array and
rendered into GeoJSON properties, and no static read of this file can tell 20 features from
16.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

# The two regions. `patchWith` and `biggestPatch` are the two flood fills and live together.
FEATS_START = "  function patchFeatures(slug"
FEATS_END = "  // Bring the patch into view first."
FILL_START = "  function biggestPatch(pts) {"
FILL_END = "  // Where on screen there is room to put the marked square."

# A crew with ground in two places, and a stranger's square between them.
#
#   a: a 5x5 block at 100,100 (twenty-five squares) and a 2x2 at 200,200 (four)
#   b: one square at 150,150, which must never light
#
# Odd-sided on purpose: a 5x5 has a square AT its centre, so "the wave starts in the middle"
# is a d of exactly 0 rather than an argument about how near the middle is near enough.
BIG = [(x, y) for x in range(100, 105) for y in range(100, 105)]
SMALL = [(x, y) for x in range(200, 202) for y in range(200, 202)]

HARNESS = """
// --- stubs. Only what the two functions touch. --------------------------------------------
const TERR = {
  z: 14,
  crews: [{ slug: "a" }, { slug: "b" }],
  // Five numbers per square, of which these functions read the first three.
  cells: %(cells)s,
};
const map = { getLayer: () => true };
// A marker rather than a ring: it is how a feature is mapped back to the square it came from.
const tileRing = (x, y, z) => [[[x, y]]];

%(feats)s

%(fill)s

const tileOf = f => f.geometry.coordinates[0][0];
const look = p => p && ({
  feats: p.feats.map(tileOf),
  dists: p.feats.map(f => f.properties.d),
  patch: p.patch.map(q => [q[0], q[1]]),
});

const out = {
  // No tap: a list row, which has no square to anchor to.
  none: look(patchFeatures("a")),
  // A tap inside the small patch, and inside the big one.
  small: look(patchFeatures("a", [200, 200])),
  big: look(patchFeatures("a", [101, 101])),
  // A square this crew does not hold at all.
  foreign: look(patchFeatures("a", [150, 150])),
  other: look(patchFeatures("b")),
};
console.log(JSON.stringify(out));
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


def _cells():
    rows = []
    for x, y in BIG + SMALL:
        rows += [0, x, y, 0, 0]
    rows += [1, 150, 150, 0, 0]
    return json.dumps(rows)


def _run():
    js = HARNESS % {
        "cells": _cells(),
        "feats": _region(FEATS_START, FEATS_END, ["patchWith", "Math.min(1,"]),
        "fill": _region(FILL_START, FILL_END, ["function patchWith("]),
    }
    d = pathlib.Path(tempfile.mkdtemp(prefix="hlscope-"))
    try:
        f = d / "t.mjs"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _tiles(rows):
    return sorted((int(a), int(b)) for a, b in rows)


def test_highlight_lights_every_square_the_crew_holds():
    """The whole report. Twenty squares held, twenty squares lit, from any of the gestures."""
    out = _run()
    want = _tiles([list(q) for q in BIG + SMALL])
    for how in ("none", "small", "big", "foreign"):
        got = _tiles(out[how]["feats"])
        assert got == want, (
            f"pressing Highlight ({how}) lit {len(got)} of the {len(want)} squares this crew "
            f"holds; the ones left dark are {sorted(set(want) - set(got))}")


def test_another_crews_ground_is_not_lit_by_accident():
    """The flat `cells` array is read five numbers at a time and the first is the crew, so
    lighting everything is one dropped index away from lighting the whole map."""
    out = _run()
    assert (150, 150) not in _tiles(out["none"]["feats"]), (
        "a square belonging to another crew was lit")
    assert _tiles(out["other"]["feats"]) == [(150, 150)], (
        f"the other crew lit {_tiles(out['other']['feats'])}, and it holds one square")


def test_the_camera_gets_the_patch_under_your_finger():
    """`framePatch` reads `patch`, and this is the half that stops the animation running away
    from the square that started it: tap the 2x2 outpost and the camera frames the outpost."""
    out = _run()
    assert _tiles(out["small"]["patch"]) == _tiles([list(q) for q in SMALL]), (
        f"a tap on the small patch framed {len(out['small']['patch'])} squares, which is not "
        "the four it is made of")
    assert _tiles(out["big"]["patch"]) == _tiles([list(q) for q in BIG]), (
        f"a tap inside the big patch framed {len(out['big']['patch'])} squares")


def test_with_nothing_tapped_the_biggest_patch_is_still_the_one_framed():
    """A row in Other crews or on the board has no square to anchor to, and the figure those
    rows print is the biggest patch -- so that is what they frame. Unchanged behaviour, kept
    honest: this is the case the old code did for everybody."""
    out = _run()
    assert _tiles(out["none"]["patch"]) == _tiles([list(q) for q in BIG]), (
        f"with no tap the camera framed {len(out['none']['patch'])} squares rather than the "
        f"{len(BIG)} of the crew's biggest patch")


def test_a_tap_on_ground_this_crew_does_not_hold_falls_back_to_its_biggest_patch():
    """Reachable: the popup is built from the square under the pointer and the handler runs
    after a rebuild could have moved the border. A patch of none is a `fitBounds` over an
    empty bounds, which throws, and the animation would be lost."""
    out = _run()
    assert _tiles(out["foreign"]["patch"]) == _tiles([list(q) for q in BIG]), (
        "a tap on a square the crew does not hold did not fall back to its biggest patch")


def test_the_sweep_still_crosses_the_framed_patch_from_the_middle_outward():
    """`d` is the only thing the wave reads, and it is normalised over the FRAMED patch.

    Normalising over everything the crew holds would be the quiet way to break the animation
    nobody would notice in a test that only counted features: 0 at the middle of Copenhagen
    and 1 at a square in Oslo makes every square on screen land within a hundredth of each
    other, so the patch you are looking at flashes all at once and the remaining 1.7s of
    sweep happens 600km away. Distant ground is clamped to 1 instead, which lights it with
    the rim of the patch on screen and keeps it lit through the tail.
    """
    out = _run()
    # Measured on the tap inside the 5x5, which is the patch that HAS a middle.
    big = {tuple(q) for q in _tiles([list(q) for q in BIG])}
    inside, outside = [], []
    for tile, d in zip(_tiles_in_order(out["big"]["feats"]), out["big"]["dists"]):
        (inside if tile in big else outside).append(d)
    assert inside and outside, "the fixture no longer has ground both inside and outside"
    assert max(inside + outside) <= 1.0 + 1e-9, (
        f"a square is at d={max(inside + outside)}, past the end of the wave's own range")
    assert min(inside) == 0, (
        f"the middle square of the framed patch is at d={min(inside):.3f}, so the wave starts "
        "somewhere other than the middle of what you are looking at")
    assert max(inside) > 0.9, (
        f"the framed patch spans d={min(inside):.3f} to {max(inside):.3f}; the sweep no longer "
        "crosses it")
    assert all(d == 1.0 for d in outside), (
        f"ground outside the framed patch is at {sorted(set(outside))}, not clamped to the rim")


def _tiles_in_order(rows):
    return [(int(a), int(b)) for a, b in rows]


def test_the_popup_hands_the_highlight_the_square_that_was_tapped():
    """The anchor is useless unless the one gesture that HAS a square passes it.

    node cannot see this: the popup is built by maplibre on `addTo` and the handler is wired
    afterwards, so the wiring is checked where it is written.
    """
    src = CREWS_JS.read_text(encoding="utf-8")
    assert "bindPopActs(tileAt(" in src, (
        "the popup does not hand its own square to the actions it binds, so Highlight cannot "
        "know what you tapped")
    assert "function bindPopActs(at)" in src, (
        "bindPopActs does not take the tapped square")
    assert "blink(slug, at)" in src, (
        "the popup's Highlight button calls blink without the square that was tapped")
    assert "function blink(slug, anchor)" in src, "blink does not accept an anchor"
