# -*- coding: utf-8 -*-
"""The generated crew emblem draws the crew's pattern.

Erwin: the little square beside a crew name showed the colour and not the pattern.
`placeholder_emblem` never looked at `clan.pattern` -- it drew a hash of the NAME as mirrored
squares with the initials on top, so the one mark a crew actually chose was the one thing its
emblem did not carry, and two crews on the same colour were told apart only by two letters.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services import crews  # noqa: E402


def test_every_pattern_draws_something_different():
    seen = {}
    for p in crews.PATTERNS:
        svg = crews.placeholder_emblem("Arctic Wheelers", "#008080", p)
        assert svg.startswith("<svg") and svg.endswith("</svg>"), p
        assert "#008080" in svg, "the crew's colour is still the ground"
        seen[p] = svg
    bodies = {p: s for p, s in seen.items() if p != "solid"}
    assert len(set(bodies.values())) == len(bodies), (
        "two patterns produced identical emblems: " + repr(
            [p for p in bodies if list(bodies.values()).count(bodies[p]) > 1]))


def test_solid_is_just_the_colour():
    svg = crews.placeholder_emblem("X", "#008080", "solid")
    assert "<pattern" not in svg and "url(#" not in svg


def test_an_unknown_pattern_falls_back_to_solid():
    assert "url(#" not in crews.placeholder_emblem("X", "#008080", "nonsense")
    assert "url(#" not in crews.placeholder_emblem("X", "#008080", None)


def test_the_name_no_longer_decides_what_is_drawn():
    """It used to be a sha256 of the name. Two crews, same identity, same emblem."""
    a = crews.placeholder_emblem("Arctic Wheelers", "#008080", "dots")
    b = crews.placeholder_emblem("Totally Different Name", "#008080", "dots")
    assert a == b


def test_no_initials_are_drawn():
    for p in ("solid", "dots", "grid"):
        assert "<text" not in crews.placeholder_emblem("Arctic Wheelers", "#008080", p)


def test_each_pattern_gets_its_own_id():
    """Served as its own file the ids cannot collide, but inlined -- a list of crews in one
    document -- `url(#p)` resolves to the FIRST #p on the page and every crew wears the first
    one's pattern. Found by rendering all twelve together and watching dots, rings and checker
    all come out as diagonal stripes."""
    ids = set()
    for p in crews.PATTERNS:
        svg = crews.placeholder_emblem("X", "#008080", p)
        if "url(#" in svg:
            ids.add(svg.split("url(#")[1].split(")")[0])
    assert len(ids) == len([p for p in crews.PATTERNS if p != "solid"]), ids


def test_the_endpoint_passes_the_pattern():
    src = (ROOT / "web" / "crews_api.py").read_text(encoding="utf-8")
    assert "placeholder_emblem(clan.name, clan.colour, clan.pattern)" in src
