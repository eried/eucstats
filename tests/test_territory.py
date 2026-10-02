"""The two rules that decide what territory looks like, and the tie-break that keeps it still."""
from services.territory import emblem_slot, regions, seeded, winners


def test_a_single_tile_claims_nothing():
    """One ride down one street must not paint a crew's colour on the map.

    This is the rule that keeps the map readable: with 96 colour-and-pattern combinations in
    play, lone tiles scattered across a continent are noise, and no crew earned them.
    """
    assert seeded({(5, 5)}) == set()
    assert seeded({(5, 5), (5, 6), (5, 7)}) == set()        # a line is still not a claim
    assert seeded({(5, 5), (6, 6)}) == set()                # touching at a corner is not


def test_a_2x2_block_claims_and_then_grows_by_contact():
    held = {(0, 0), (1, 0), (0, 1), (1, 1),                 # the seed
            (2, 1), (3, 1),                                 # a spur reaching out from it
            (9, 9)}                                         # and an island that touches nothing
    out = seeded(held)
    assert (0, 0) in out and (1, 1) in out
    assert (2, 1) in out and (3, 1) in out, "contact with a seed should carry territory"
    assert (9, 9) not in out, "an unconnected tile is dropped, not shown faintly"


def test_corner_contact_does_not_carry_territory():
    """Two blocks meeting at a point are not one region to anybody looking at the map."""
    held = {(0, 0), (1, 0), (0, 1), (1, 1),
            (2, 2), (3, 2), (2, 3), (3, 3)}                 # diagonal neighbour block
    out = seeded(held)
    assert len(regions(out)) == 2, "diagonal blocks are two regions, not one"


def test_emblem_sits_square_and_centred():
    """A 3x2 block holds two 2x2 squares; the emblem takes the one nearer the middle.

    An emblem drawn into a non-square slot comes out stretched, which is the reason territory
    uses Mercator tiles at all. So the slot is always square, and where there is a choice it is
    the centred one.
    """
    block = {(x, y) for x in range(3) for y in range(2)}
    x, y, size = emblem_slot(block)
    assert size == 2, "the largest square inside 3x2 is 2x2"
    assert (x, y) in ((0, 0), (1, 0))
    # centre of the chosen square against centre of the region: within half a tile
    assert abs((x + size / 2) - 1.5) <= 0.5

    big = {(x, y) for x in range(6) for y in range(6)}
    assert emblem_slot(big)[2] == 6


def test_emblem_avoids_a_hole():
    """A region with a gap in the middle cannot take a square that covers the gap."""
    comp = {(x, y) for x in range(5) for y in range(5)}
    comp.discard((2, 2))
    x, y, size = emblem_slot(comp)
    assert size <= 2
    covered = {(x + i, y + j) for i in range(size) for j in range(size)}
    assert (2, 2) not in covered


def test_ties_go_to_whoever_held_it_first():
    """Equal kilometres must not hand a tile back and forth on every rebuild."""
    acc = {"13/1/1": {"A": [10.0, {"r1"}], "B": [10.0, {"r2"}]}}
    assert winners(acc, previous={"13/1/1": "B"})["13/1/1"][0] == "B"
    assert winners(acc, previous={"13/1/1": "A"})["13/1/1"][0] == "A"


def test_more_kilometres_takes_the_tile_from_the_incumbent():
    """Ties are sticky, but a real lead is not — this is meant to be takeable."""
    acc = {"13/1/1": {"A": [40.0, {"r1"}], "B": [10.0, {"r2"}]}}
    assert winners(acc, previous={"13/1/1": "B"})["13/1/1"][0] == "A"
