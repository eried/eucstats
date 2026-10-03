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
for (const [W, H] of %s) {
  global.window = { innerWidth: W, innerHeight: H };
  const pw = Math.min(W * 0.94, 720);          // .panel  width: min(94vw, 720px)
  const ph = Math.min(H * 0.76, 760);          //         height: 76dvh, max 760
  const panel = rect((W - pw) / 2, H - 84 - ph, pw, ph);   // bottom: 84px, centred
  const topbar = rect(16, 16, Math.min(W * 0.92, 380), 168);
  const dock = rect((W - 420) / 2, H - 70, 420, 52);
  const boxes = { ".panel": panel, ".topbar": topbar, ".dock": dock };
  global.document = {
    querySelector: (s) => (boxes[s] ? { getBoundingClientRect: () => boxes[s] } : null) };

  let off = freeOffset();
  const closes = off === null;                 // no room: the caller hides the panel
  if (closes) off = [0, 0];
  const x = W / 2 + off[0], y = H / 2 + off[1];
  const hidden = closes ? [] : Object.entries(boxes).filter(([, b]) =>
      x >= b.left && x <= b.right && y >= b.top && y <= b.bottom).map(([k]) => k);
  out.push({ W, H, closes, point: [Math.round(x), Math.round(y)], hidden });
}
console.log(JSON.stringify(out));
"""


def _run():
    src = CREWS_JS.read_text(encoding="utf-8")
    start = src.index("  function freeOffset() {")
    end = src.index("  function flyToTile(x, i) {")
    d = pathlib.Path(tempfile.mkdtemp(prefix="flyto-"))
    try:
        f = d / "t.js"
        f.write_text(HARNESS % (src[start:end], json.dumps(VIEWPORTS)), encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_the_marked_square_never_lands_under_the_furniture():
    for row in _run():
        assert not row["hidden"], (
            f"{row['W']}x{row['H']}: the square lands at {row['point']}, "
            f"under {row['hidden']}")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_the_panel_only_closes_when_there_is_genuinely_nowhere():
    """Closing it is the fallback, not the habit: on anything roomier than a phone held
    sideways the camera should find space and leave the card where the rider put it."""
    rows = {(r["W"], r["H"]): r for r in _run()}
    assert rows[(1440, 900)]["closes"] is False
    assert rows[(1024, 768)]["closes"] is False
    assert rows[(768, 1024)]["closes"] is False
    assert rows[(600, 800)]["closes"] is False
