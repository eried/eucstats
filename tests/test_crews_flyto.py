"""The square you tapped has to land somewhere you can see it.

"Tap it to find it" is the one verb the targets card exists for, and three versions of the
camera offset aimed at a named direction and were each defeated by a viewport nobody had
measured:

  * the panel's own centre -- identically zero, because the panel is centred;
  * whichever margin was wider -- there is none between 561 and 1080;
  * the region above the panel -- which is where the topbar lives. A reviewer measured the
    ring 100% hidden under it at 600x800, 32% at iPad portrait, and back behind the panel at
    phone-landscape, where the guard that chose this branch was false anyway.

Each fix was verified at the one size that prompted it. So the geometry is checked at every
size the reviewers reported, in the browser's own arithmetic: the shipped `freeOffset()` is
lifted out of crews.js and run under node against the real CSS boxes. It needs no browser,
which is the point -- a viewport nobody thought to open is exactly how this broke three times.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

# width, height: every size a reviewer reported on, plus the common desktop ones. 390x844 is
# the portrait phone, and it is here because without it the `innerWidth <= 560` early return --
# the most common screen in the set -- was the one path nothing exercised.
VIEWPORTS = [
    (1440, 900), (1280, 800), (1080, 900), (1024, 768), (900, 540),
    (844, 390), (820, 1180), (768, 1024), (760, 768), (600, 800), (561, 768), (390, 844),
]

HARNESS = """
%s
function rect(l, t, w, h) {
  return { left: l, top: t, right: l + w, bottom: t + h, width: w, height: h };
}
const out = [];
const CROWDED = process.argv[2] === "crowded";
for (const [W, H] of %s) {
  global.window = { innerWidth: W, innerHeight: H };
  const pw = Math.min(W * 0.94, 720);          // .panel  width: min(94vw, 720px)
  // Both halves of the same breakpoint, because they are one decision: below 620px tall the
  // panel reserves 160px instead of 300 (`--panres`) and the topbar drops to its badge
  // (`.topbar.tight`). Hard-coding `H - 300` here was a snapshot of a stylesheet that has
  // since moved, and a reviewer measured 38px of panel at 844x390 while this still said 90.
  // One synthetic shape, and it is the only one here: an obstacle across the middle of the
  // window with space on both sides. See test_the_second_look_measures_rather_than_giving_up.
  const FALLBACK = process.argv[2] === "fallback";
  const TIGHT = H <= 620;
  const reserve = TIGHT ? 160 : 300;
  // Two shapes: the panel as it ships, and a deliberately crowded one. The crowded case is
  // what exercises the tight fit -- capping the panel freed so much space that a point-sized
  // clearance test passes on the shipped geometry, so a harness using only that would certify
  // the very bug a reviewer measured in Chrome.
  const ph = (CROWDED || FALLBACK) ? Math.min(H * 0.88, 900)
                                  : Math.min(Math.min(H * 0.76, H - reserve), 760);
  const panel = rect((W - pw) / 2, H - 84 - ph, pw, ph);   // bottom: 84px, centred
  // 48 rather than the measured ~42 for the badge: an obstacle modelled larger than it is can
  // only make this test harder to pass.
  const topbar = FALLBACK ? rect(W / 2 - 200, 16, 400, H * 0.58)
                          : rect(16, 16, Math.min(W * 0.92, 380), TIGHT ? 48 : 168);
  // Measured, not guessed: 802px at desktop and 690 below the 880px query that
  // tightens the buttons. It was modelled at 420 -- half the real obstacle, which is
  // the wrong direction for a harness to be wrong in.
  const dockW = Math.min(W - 16, W <= 880 ? 690 : 802);
  const dock = rect((W - dockW) / 2, H - 70, dockW, 52);
  const boxes = { ".panel": panel, ".topbar": topbar, ".dock": dock };
  global.document = {
    querySelector: (s) => (boxes[s] ? { getBoundingClientRect: () => boxes[s] } : null) };

  // The REAL hook, timed the way the page times it: `closePanel()` starts a 280ms slide and
  // takes the class off at the end of it, so nothing the decision can measure has changed
  // while it runs. Deleting the box synchronously here is what let this file certify a
  // fallback that could not run -- a reviewer restored the real timing and the crowded case
  // went red at 900x540.
  let closes = false;
  global.H = { closePanel: () => {
    closes = true;
    setTimeout(() => { delete boxes[".panel"]; }, 280);
  } };
  // At phone width the caller closes the panel BEFORE asking -- see flyToTile -- so the
  // early return is measured against a screen the panel is leaving, which is the real case.
  // `global.H`, not `H`: inside this loop `H` is the viewport height. The shipped functions
  // are declared at module scope, so their own `H` is the helper object either way.
  const phone = W <= 560;
  if (phone) global.H.closePanel();
  const off = offsetForFlight();
  const x = W / 2 + off[0], y = H / 2 + off[1];
  // The RING, not the point. The map draws a 112px square; a centre that is merely "clear"
  // can still hang 56px of it over the panel or off the edge of the window, which is how a
  // harness reported 11 of 11 clean on a build Chrome failed at 4 of 12.
  const R = 56;
  const ring = { left: x - R, top: y - R, right: x + R, bottom: y + R };
  // A panel that has been told to close is on its way out, so it is not an obstacle to the
  // square the camera is flying to -- the 280ms slide finishes long before the flight does.
  const hidden = Object.entries(boxes).filter(([k, b]) =>
      !(closes && k === ".panel") &&
      ring.left < b.right && ring.right > b.left &&
      ring.top < b.bottom && ring.bottom > b.top).map(([k]) => k);
  const clipped = ring.left < 0 || ring.top < 0 || ring.right > W || ring.bottom > H;
  out.push({ W, H, closes, phone, point: [Math.round(x), Math.round(y)], hidden, clipped });
}
console.log(JSON.stringify(out));
"""


def _run(crowded=False, mode=None):
    src = CREWS_JS.read_text(encoding="utf-8")
    start = src.index("  function freeOffset(skip) {")
    end = src.index("  function flyToTile(x, i) {")   # takes offsetForFlight() with it
    d = pathlib.Path(tempfile.mkdtemp(prefix="flyto-"))
    try:
        f = d / "t.js"
        f.write_text(HARNESS % (src[start:end], json.dumps(VIEWPORTS)), encoding="utf-8")
        argv = ["node", str(f)] + ([mode or "crowded"] if (crowded or mode) else [])
        r = subprocess.run(argv, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_the_marked_square_never_lands_under_the_furniture():
    for row in _run():
        assert not row["hidden"], (
            f"{row['W']}x{row['H']}: the marked square overlaps {row['hidden']}")
        assert not row["clipped"], (
            f"{row['W']}x{row['H']}: the marked square is cut off by the window edge")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_the_panel_only_closes_when_there_is_genuinely_nowhere():
    """Closing it is the fallback, not the habit: on anything roomier than a phone held
    sideways the camera should find space and leave the card where the rider put it."""
    rows = {(r["W"], r["H"]): r for r in _run()}
    assert rows[(1440, 900)]["closes"] is False
    assert rows[(1024, 768)]["closes"] is False
    assert rows[(768, 1024)]["closes"] is False
    assert rows[(600, 800)]["closes"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_the_square_still_clears_when_the_screen_is_crowded():
    """The case that actually tests the fit.

    A reviewer measured the marked square overlapping the panel or hanging off the window at
    four viewports in twelve, while a harness that asserted on the centre POINT reported all
    of them clean. Capping the panel later freed enough room that the shipped geometry passes
    either way, so the tight path needs a crowded screen to exercise it: the square is 112px
    across and the clearance test has to know that.
    """
    for row in _run(crowded=True):
        assert not row["hidden"], (
            f"{row['W']}x{row['H']} crowded: the marked square overlaps {row['hidden']}")
        assert not row["clipped"], (
            f"{row['W']}x{row['H']} crowded: the marked square is cut off by the window edge")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_the_second_look_measures_rather_than_giving_up():
    """The fallback branch, on the one shape that needs it.

    Everything above runs the shipped geometry, where -- since the topbar drops to a badge on
    short screens -- dead centre happens to be clear at all twelve sizes even with the panel
    crowded. That makes `|| [0, 0]` right by luck and the branch untested: I removed the
    exclusion and all three cases still passed.

    This layout is synthetic and is the only synthetic one here: an obstacle across the middle
    with room on both sides. It asks exactly what the branch is for -- the panel is going, the
    middle is still no good -- and the answer has to be a measured offset. Returning the centre
    fails it, which is what removing the exclusion does, because the second look would then be
    measuring a panel that is still mid-slide.
    """
    # the caller closes the panel itself at phone width, so the early return is its own case
    rows = [r for r in _run(mode="fallback") if not r["phone"]]
    assert rows, "no rows"
    fell_back = [r for r in rows if r["closes"]]
    # Without this the test proves nothing: on a wide screen the first look finds room beside
    # the obstacle and the branch never runs.
    assert fell_back, ("no viewport in this layout drove the panel shut, so the second look "
                       "was never reached and this case is not testing it")
    for row in fell_back:
        assert row["point"] != [row["W"] // 2, row["H"] // 2], (
            f"{row['W']}x{row['H']}: gave up and flew to dead centre, which is behind the "
            f"obstacle it was supposed to measure around")
    for row in rows:
        assert not row["hidden"], (
            f"{row['W']}x{row['H']}: the marked square overlaps {row['hidden']}")
        assert not row["clipped"], (
            f"{row['W']}x{row['H']}: the marked square is cut off by the window edge")
