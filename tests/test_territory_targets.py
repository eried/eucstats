# -*- coding: utf-8 -*-
"""A target has to be somewhere a wheel can go.

Erwin, with a screenshot of Tromso: "2 of the 4 we show are in pure water where if I map ALL my
rides there is no single ride near them". The filter kept any candidate in `rideable`, which is
every ridden square PLUS its four neighbours -- so a sea square touching the crew's own coast
passed. Measured on that board, his two land squares had both been ridden and his two sea ones
never had, which is the whole of the fix: a square somebody has ridden in is dry.

The fringe is still there as a fallback, because a crew whose every candidate is new ground
must not be handed an empty card. These pin both halves.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _filter(cand, block, ridden, rideable):
    """The rule as `targets_for` applies it, isolated from everything around it."""
    if ridden:
        been = {q for q in cand if q in ridden or q in block}
        if been:
            return been
        if rideable:
            fringe = {q for q in cand if q in rideable or q in block}
            if fringe:
                return fringe
    elif rideable:
        fringe = {q for q in cand if q in rideable or q in block}
        if fringe:
            return fringe
    return cand


def test_the_sea_squares_from_tromso_are_refused():
    """The real four, by their real tile coordinates."""
    land = {(9052, 3712), (9052, 3715)}       # both ridden, on the board Erwin photographed
    sea = {(9051, 3710), (9051, 3713)}        # neither ever ridden
    cand = land | sea
    # the dilated set lets the sea in: each of those squares touches ridden ground
    rideable = set(land) | {(9051, 3710), (9051, 3713), (9052, 3711)}
    got = _filter(cand, block=set(), ridden=land, rideable=rideable)
    assert got == land, "a square nobody has ridden in is not a place to send somebody"


def test_a_crew_with_no_ridden_candidate_still_gets_told_where_to_go():
    """Why the fringe survives. An empty card is worse than an imperfect one."""
    cand = {(10, 10), (10, 11)}
    got = _filter(cand, block=set(), ridden={(9, 10)}, rideable={(9, 10), (10, 10)})
    assert got == {(10, 10)}, "the fringe has to carry a crew that has ridden nowhere near"


def test_a_brand_new_crews_block_is_never_filtered_out():
    """The 2x2 a crew holding nothing is offered brings its own route with it."""
    block = {(5, 5), (6, 5), (5, 6), (6, 6)}
    got = _filter(set(block), block=block, ridden={(5, 5)}, rideable={(5, 5)})
    assert got == block


def test_the_rule_in_the_service_is_the_rule_tested_here():
    """This file reimplements the filter, so it has to be told when the real one moves.

    It already has: the set went from the crew's own riding to everybody's, which is what
    Erwin asked for -- "if we dont have from this crew we can use data from other riders to
    validate which ones can be taken".
    """
    src = (ROOT / "services" / "territory.py").read_text(encoding="utf-8")
    assert "been = {q for q in cand if q in ridden or q in block}" in src
    assert "ridden=anybody_rode" in src, "the undilated set must reach targets_for"


def test_ridability_is_judged_on_everybodys_riding():
    """A rider in no crew still proves there is a road, and `accumulate` cannot see them:
    it filters on `Trip.clan_id.isnot(None)` because that is what scoring is about."""
    src = (ROOT / "services" / "territory.py").read_text(encoding="utf-8")
    assert "def ridden_tiles(" in src
    i, j = src.index("def ridden_tiles("), src.index("def accumulate(")
    body = src[i:j]
    assert "clan_id" not in body, (
        "ridden_tiles must NOT filter on crew attribution -- that is the whole point of it")
    assert 'validation_status == "validated"' in body, "and it must still only trust real rides"
