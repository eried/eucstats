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

# width, height: every size a reviewer reported on, plus the common desktop ones
VIEWPORTS = [
    (1440, 900), (1280, 800), (1080, 900), (1024, 768), (900, 540),
    (844, 390), (820, 1180), (768, 1024), (760, 768), (600, 800), (561, 768),
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
  // Two shapes: the panel as it ships, and a deliberately crowded one. The crowded case is
  // what exercises the tight fit -- capping the panel freed so much space that a point-sized
  // clearance test passes on the shipped geometry, so a harness using only that would certify
  // the very bug a reviewer measured in Chrome.
  const ph = CROWDED ? Math.min(H * 0.88, 900) : Math.min(Math.min(H * 0.76, H - 300), 760);
  const panel = rect((W - pw) / 2, H - 84 - ph, pw, ph);   // bottom: 84px, centred
  const topbar = rect(16, 16, Math.min(W * 0.92, 380), 168);
  const dock = rect((W - 420) / 2, H - 70, 420, 52);
  const boxes = { ".panel": panel, ".topbar": topbar, ".dock": dock };
  global.document = {
    querySelector: (s) => (boxes[s] ? { getBoundingClientRect: () => boxes[s] } : null) };

  // `H.closePanel` is the real hook: taking the panel out of the obstacle set is exactly what
  // closing it does, so the shipped decision runs unmodified against it.
  let closes = false;
  global.H = { closePanel: () => { closes = true; delete boxes[".panel"]; } };
  const off = offsetForFlight();
  const x = W / 2 + off[0], y = H / 2 + off[1];
  // The RING, not the point. The map draws a 112px square; a centre that is merely "clear"
  // can still hang 56px of it over the panel or off the edge of the window, which is how a
  // harness reported 11 of 11 clean on a build Chrome failed at 4 of 12.
  const R = 56;
  const ring = { left: x - R, top: y - R, right: x + R, bottom: y + R };
  const hidden = Object.entries(boxes).filter(([, b]) =>
      ring.left < b.right && ring.right > b.left &&
      ring.top < b.bottom && ring.bottom > b.top).map(([k]) => k);
  const clipped = ring.left < 0 || ring.top < 0 || ring.right > W || ring.bottom > H;
  out.push({ W, H, closes, point: [Math.round(x), Math.round(y)], hidden, clipped });
}
console.log(JSON.stringify(out));
"""


def _run(crowded=False):
    src = CREWS_JS.read_text(encoding="utf-8")
    start = src.index("  function freeOffset() {")
    end = src.index("  function flyToTile(x, i) {")   # takes offsetForFlight() with it
    d = pathlib.Path(tempfile.mkdtemp(prefix="flyto-"))
    try:
        f = d / "t.js"
        f.write_text(HARNESS % (src[start:end], json.dumps(VIEWPORTS)), encoding="utf-8")
        argv = ["node", str(f)] + (["crowded"] if crowded else [])
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
