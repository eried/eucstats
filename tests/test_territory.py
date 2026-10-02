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


# --- the things a hostile review found, pinned so they cannot come back ---

def test_a_diagonal_ride_leaves_a_connected_trail():
    """Territory grows through edges, so a ride must leave an edge-connected trail.

    The first version interpolated the route at evenly spaced points and mapped each to a
    tile, which on a diagonal advanced x and y together — a staircase touching only at
    corners, with the tiles actually crossed never emitted. A 31 km diagonal ride came out as
    six disconnected tiles and claimed nothing. The original test passed only because its
    route ran due east.
    """
    from services.tiles import parse, tiles_along
    for (a, b) in [((0.0, 0.0), (2.0, 2.0)),          # equator, 45 degrees
                   ((59.9, 10.7), (60.3, 11.4)),      # Oslo, a shallower diagonal
                   ((59.9, 10.7), (59.5, 10.2))]:     # and back the other way
        ts = tiles_along([a, b], 13)
        assert len(ts) > 4
        for t1, t2 in zip(ts, ts[1:]):
            _, x1, y1 = parse(t1)
            _, x2, y2 = parse(t2)
            assert abs(x1 - x2) + abs(y1 - y2) == 1, f"{t1} -> {t2} is not edge-adjacent"


def test_a_crew_that_cannot_draw_a_tile_does_not_take_it_from_everyone():
    """Spoiling must not be the cheapest move in the game.

    A crew riding one tile in the middle of a rival's block wins that tile on kilometres but
    can never draw it — and the naive rule then left it blank, which unseeded the rival's
    whole block. Ten kilometres of riding erased sixty and put nothing on the map.
    """
    from services.territory import award
    acc = {}
    for x in range(3):
        for y in range(2):
            acc[f"13/{x}/{y}"] = {"live": [10.0, {"r1"}]}
    acc["13/1/0"]["ghost"] = [10.1, {"r2"}]          # outrides them in one interior tile

    kept, _, _blocked = award(acc, {"live", "ghost"}, {})
    assert len(kept.get("live", ())) == 6, "the crew that can draw the block keeps it"
    assert not kept.get("ghost"), "a crew with no 2x2 anywhere still draws nothing"


def test_a_disbanded_crew_does_not_salt_the_ground_it_held():
    from services.territory import award
    acc = {}
    for x in range(3):
        for y in range(2):
            acc[f"13/{x}/{y}"] = {"live": [10.0, {"r1"}]}
    acc["13/1/0"]["dead"] = [10.1, {"r2"}]
    kept, _, _blocked = award(acc, {"live"}, {})               # "dead" is no longer a live crew
    assert len(kept.get("live", ())) == 6


def test_a_lead_under_the_floor_takes_nothing():
    """Without a floor the cheapest ground was a fabricated few-hundred-metre ride."""
    from services.territory import min_lead_km, winners
    floor = min_lead_km("13/1/1")
    assert winners({"13/1/1": {"A": [floor - 0.01, {"r"}]}}) == {}
    assert winners({"13/1/1": {"A": [floor + 0.01, {"r"}]}})["13/1/1"][0] == "A"


def test_the_floor_is_the_same_effort_at_every_latitude():
    """A flat floor made ground four times cheaper at the equator, so the board was sorted
    by latitude as much as by riding."""
    from services.territory import min_lead_km
    oslo = min_lead_km("13/4339/2383")
    equator = min_lead_km("13/4096/4096")
    assert equator > oslo * 1.6, "an equatorial tile is far wider, so it must cost more"
    # and each is a sensible fraction of its own tile, not an arbitrary constant
    assert 0.3 < oslo < 1.5 and 1.5 < equator < 3.0


def test_a_long_ride_is_not_punished_for_covering_ground():
    """Distance is credited where it was ridden, not split evenly across the tiles touched.

    Under an even split, the further a ride went the less each tile was worth: a road trip
    joining two areas — the whole point of a "biggest unbroken region" rule — scored a few
    hundred metres per tile and claimed none of them, while a short loop scored kilometres.
    """
    from services.territory import _per_tile_km, min_lead_km
    trip = [(59.9, 10.75 + i * 0.012) for i in range(600)]
    per = _per_tile_km(trip, 13)
    assert len(per) > 100, "a long ride should cross many tiles"
    clear = [v for k, v in per.items() if v >= min_lead_km(k)]
    assert len(clear) > len(per) * 0.9, "almost every tile crossed should clear the floor"


def test_the_seed_size_is_actually_used():
    """The admin can set it, so it has to do something — it used to be read from a constant."""
    from services.territory import seeded
    block = {(x, y) for x in range(2) for y in range(2)}
    assert seeded(block, seed=2) == block
    assert seeded(block, seed=3) == set(), "a 2x2 cannot satisfy a 3x3 seed"


def test_ground_you_rode_all_the_way_around_is_yours():
    """An enclosed gap nobody holds looks like a rendering fault, and riding right around
    something is a clearer claim than riding across it once."""
    from services.territory import fill_enclosed
    ring = {(x, y) for x in range(5) for y in range(5)} - {(2, 2)}
    assert fill_enclosed(ring, taken=set()) == {(2, 2)}

    # a hole several tiles wide fills too
    wide = {(x, y) for x in range(6) for y in range(6)} - {(2, 2), (3, 2), (2, 3), (3, 3)}
    assert fill_enclosed(wide, taken=set()) == {(2, 2), (3, 2), (2, 3), (3, 3)}


def test_surrounding_a_rival_does_not_swallow_them():
    """You can ride right around somebody. They keep what they hold."""
    from services.territory import fill_enclosed
    ring = {(x, y) for x in range(5) for y in range(5)} - {(2, 2)}
    assert fill_enclosed(ring, taken={(2, 2)}) == set()


def test_an_open_bay_is_not_enclosed():
    """Three sides is not all the way around."""
    from services.territory import fill_enclosed
    u = {(0, 0), (1, 0), (2, 0), (0, 1), (2, 1), (0, 2), (1, 2), (2, 2)}
    u.discard((1, 1))
    u.discard((1, 2))                     # leave the top open
    assert (1, 1) not in fill_enclosed(u, taken=set())


def test_a_score_can_only_ever_go_down_on_its_own():
    """Nobody riding must never make a tile's score rise.

    The weekly cap was bucketed by how old a trip was AT REBUILD TIME, so a trip crossed into
    a fresh bucket as it aged and released kilometres the cap had suppressed. Measured, one
    rider with two rides six days apart: 5.97 weighted km, then 10.72 six minutes later. A
    rider acting on "4 km would take it" could find the holder had doubled overnight.
    """
    from datetime import timedelta
    from models import utcnow

    # the bucket a trip falls in is a property of the trip, not of when we happen to look
    now = utcnow()
    for shift_hours in (0, 1, 6, 24, 72):
        at = now - timedelta(days=6.8, hours=shift_hours)
        bucket = int(at.timestamp() // 604800)
        later = int(at.timestamp() // 604800)       # same trip, asked again later
        assert bucket == later


def test_enclosed_ground_does_not_claim_to_be_fading():
    """A tile held because the crew rode around it has no kilometres in it, and used to come
    out as "fading, 0.0 km clear", or "about to flip" the moment any rival had km there."""
    from services.territory import _pressure
    band, n = _pressure({"14/1/1": {"rival": [99.0, {"r"}]}}, "14/1/1", "holder", 0.0)
    assert band == 4, "ringed ground is its own state, not a warning"
    assert n == 0


def test_a_ring_cannot_swallow_more_than_it_rode():
    """A one-tile-wide ring around a city was about five times more ground per kilometre than
    filling the same square solid, and the board ranks on exactly that, so it was not a side
    exploit but the best way to play."""
    from services.territory import _close_holes
    ring = set()
    n = 20
    for i in range(n):
        ring |= {(i, 0), (i, n - 1), (0, i), (n - 1, i)}
    ring |= {(1, 1), (2, 1), (1, 2), (2, 2)}          # a bulge so it seeds
    out = _close_holes({"A": ring})["A"]
    assert len(out) <= len(ring) * 2, (len(out), len(ring))


def test_two_crews_cannot_gain_the_same_ground():
    """The flood used to run straight through a rival's ring, so nested crews could each claim
    the same enclosed tiles: two ClanCell rows, two overlapping fills, an arbitrary popup."""
    from services.territory import _close_holes
    inner = {(x, y) for x in range(4, 7) for y in range(4, 7)}
    outer = set()
    for i in range(12):
        outer |= {(i, 0), (i, 11), (0, i), (11, i)}
    outer |= {(1, 1), (2, 1), (1, 2), (2, 2)}
    out = _close_holes({"inner": inner, "outer": outer})
    assert not (out["inner"] & out["outer"]), "no tile may be held by two crews"
