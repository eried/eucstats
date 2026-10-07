# -*- coding: utf-8 -*-
"""A crew's share QR wears the crew's emblem, so it has to survive wearing it.

Erwin asked for the crew's image in the middle of the code. Drawing anything on a QR destroys
the modules underneath, and whether the code still reads is a property of its error correction
level, not of how careful the CSS is. At the default level (M, ~15% recoverable) the emblem
this project draws -- 23% of the side, about 5% of the area, on a white plate -- makes the code
undecodable. It is not marginal and it is not a judgement call: cv2 refuses it outright.

So `pairing.qr_rows(robust=True)` raises it to H (~30%), and this is the test that says the
raise is load-bearing. Remove `robust=True` from `crews_api` and the second case here fails.

cv2 is the only thing in the suite that reads a QR rather than writing one, and it is a heavy
import, so the decode cases skip cleanly where it is absent; the structural case does not.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services import pairing  # noqa: E402

URL = "https://eucstats.ried.no/c/arctic-wheelers"

cv2 = pytest.importorskip("cv2", reason="opencv reads the code back; encoding does not need it")
np = pytest.importorskip("numpy")


def _render(rows, logo_fraction=0.0, px=8):
    """The grid as the panel draws it, optionally with the emblem plate over the middle."""
    from PIL import Image, ImageDraw

    n = len(rows)
    im = Image.new("L", (n * px, n * px), 255)
    d = ImageDraw.Draw(im)
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell == "1":
                d.rectangle([x * px, y * px, (x + 1) * px - 1, (y + 1) * px - 1], fill=0)
    if logo_fraction:
        side = int(n * px * logo_fraction)
        ring = int(n * px * 0.015)          # the white box-shadow in .crewqrlogo
        c = n * px // 2
        d.rectangle([c - side // 2 - ring, c - side // 2 - ring,
                     c + side // 2 + ring, c + side // 2 + ring], fill=255)
        d.ellipse([c - side // 2, c - side // 2, c + side // 2, c + side // 2], fill=90)
    return np.array(im)


def _decode(img):
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return text


def test_a_plain_code_reads_at_either_level():
    for robust in (False, True):
        assert _decode(_render(pairing.qr_rows(URL, robust=robust))) == URL, robust


def test_the_default_level_cannot_carry_the_emblem():
    """Why robust= exists. If this ever passes, the emblem got smaller or qrcode changed."""
    got = _decode(_render(pairing.qr_rows(URL, robust=False), logo_fraction=0.23))
    assert got != URL, (
        "A default-correction code now survives the emblem. That is good news, but this test "
        "is the reason crews_api asks for robust=True -- check whether it is still needed."
    )


def test_the_robust_level_carries_it():
    got = _decode(_render(pairing.qr_rows(URL, robust=True), logo_fraction=0.23))
    assert got == URL, (
        "The crew share QR no longer decodes with the crew's emblem on it. Either the error "
        "correction dropped below H or .crewqrlogo grew past what H can recover."
    )


def test_the_crew_share_code_is_the_robust_one():
    """The structural half: the caller that draws a logo is the caller that asks for the room."""
    src = (ROOT / "web" / "crews_api.py").read_text(encoding="utf-8")
    assert 'pairing.qr_rows(out["crew"]["share_url"], robust=True)' in src, (
        "The crew share QR must be generated with robust=True: crews.js draws the crew's "
        "emblem over the middle of it."
    )


# --- and the shape it is drawn in ----------------------------------------------------------
# A correct matrix drawn as rectangles is still unreadable. A phone rule written for the
# sign-in card said `.crewqr img, .crewqrg { width: 124px; height: 124px }` -- the second
# selector unscoped -- so it set the height of EVERY code on the site, while the crew sheet's
# own rule widened its one to 190. On a phone, which is the device these are scanned with, a
# crew's code rendered 190x124. Nothing failed; it just stopped being a QR code.

CSS = (ROOT / "web" / "static" / "crews.css").read_text(encoding="utf-8")


def test_the_code_is_square_by_construction():
    """Not by every caller remembering to set both axes."""
    base = [ln for ln in CSS.splitlines() if ln.startswith(".crewqrg {")]
    assert base, ".crewqrg base rule not found"
    block = CSS[CSS.index(base[0]):CSS.index("}", CSS.index(base[0]))]
    assert "aspect-ratio: 1" in block, (
        "The QR grid must keep its own aspect ratio, so a rule that sets only width cannot "
        "make it a rectangle: " + block)
    assert "grid-auto-rows: 1fr" in block, (
        "Rows must share the height the way columns share the width, or the modules are "
        "rectangular even in a square box: " + block)


def test_no_unscoped_rule_sets_the_height_of_every_code():
    import re
    bad = []
    for m in re.finditer(r"^([^@{}\n][^{\n]*)\{([^}]*)\}", CSS, re.M):
        sel, body = m.group(1).strip(), m.group(2)
        if "height" not in body:
            continue
        for one in (s.strip() for s in sel.split(",")):
            # a bare `.crewqrg`, with nothing narrowing it to one surface
            if one == ".crewqrg":
                bad.append(sel.strip() + " { " + body.strip() + " }")
    assert not bad, (
        "A rule sets a height on every .crewqrg on the site. Scope it to the surface it was "
        "written for (e.g. `.crewqr .crewqrg`), or the crew sheet and the share modal inherit "
        "a height meant for the sign-in card:\n  " + "\n  ".join(bad))
