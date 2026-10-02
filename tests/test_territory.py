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


def test_the_list_points_at_ground_the_crew_does_not_hold():
    """Riders could see a tile was contested only by stumbling on it, and the tiles worth
    riding are the ones somebody else holds, which look like anywhere else from the saddle."""
    from services.territory import targets_for
    board = {"A": {(10, 10), (11, 10)}, "B": {(12, 10), (12, 11), (13, 10), (13, 11)}}
    acc = {"14/12/10": {"A": [0.3, {"r"}], "B": [4.0, {"s"}]}}
    won = {"14/12/10": ("B", 4.0, 1)}
    out = targets_for(acc, board, "A", won, 14)
    xy = {(t["x"], t["y"]) for t in out}
    assert (12, 10) in xy, "the tile the crew is second in has to be on the list"
    assert not (xy & board["A"]), "no point sending anybody where they already won"
    row = next(t for t in out if (t["x"], t["y"]) == (12, 10))
    assert row["need"] == 3.7, row            # 4.0 to beat, 0.3 already ridden
    assert row["held_by"] == "B"


def test_the_row_names_who_holds_it_not_who_leads_it():
    """A crew with no 2x2 anywhere can lead a tile on kilometres and draw none of it: the
    re-award loop withdraws the claim and the ground falls to somebody else. Reading the
    leader out of `won` put a crew that holds nothing on every row as the holder."""
    from services.territory import targets_for
    board = {"A": {(0, 0), (1, 0), (0, 1), (1, 1)}}      # B holds nothing anywhere
    acc = {"14/2/0": {"B": [9.0, {"s"}], "A": [1.0, {"r"}]}}
    won = {"14/2/0": ("B", 9.0, 1)}                       # B leads it and cannot draw it
    row = next(t for t in targets_for(acc, board, "A", won, 14)
               if (t["x"], t["y"]) == (2, 0))
    assert row["held_by"] is None, "nobody holds it, whatever the kilometres say"
    assert row["need"] == 8.0, row                        # still have to out-ride the leader


def test_a_tile_that_joins_two_patches_is_listed_first():
    """The board ranks on the biggest single patch, so the roadtrip that welds two together is
    worth more than any amount of widening either one, and the list has to say so."""
    from services.territory import targets_for
    left = {(0, 0), (1, 0), (0, 1), (1, 1)}
    right = {(3, 0), (4, 0), (3, 1), (4, 1)}
    out = targets_for(acc={}, kept={"A": left | right}, clan_id="A", won={}, zoom=14)
    assert out, "adjacent unheld ground should always give the crew somewhere to go"
    joiners = {(t["x"], t["y"]) for t in out if t["joins"]}
    assert joiners == {(2, 0), (2, 1)}, joiners
    assert all(t["joins"] for t in out[:len(joiners)]), "joiners come before the rest"


def test_growing_the_ranked_patch_beats_growing_a_spare_one():
    """Widening the little patch across town is real ground and moves nothing on the board,
    which ranks on the biggest single area. The list has to put those in the right order.

    The flag itself never reaches the client: it was true of ten rows in ten for almost every
    crew, so it sorts the list and stays off the screen and out of the payload."""
    from services.territory import targets_for
    big = {(x, y) for x in range(5) for y in range(5)}
    spare = {(40, 40), (41, 40), (40, 41), (41, 41)}
    out = targets_for(acc={}, kept={"A": big | spare}, clan_id="A", won={}, zoom=14, limit=40)
    assert "grows" not in out[0], "sort key, not payload"
    first_spare = next(i for i, t in enumerate(out) if t["x"] > 10)
    assert all(t["x"] < 10 for t in out[:first_spare]), out[:first_spare]
    assert first_spare >= 8, "the ranked patch has twenty neighbours; they all come first"


def test_a_badge_that_fires_on_every_row_says_nothing():
    """The first cut marked every row "completes a 2x2": with 90 tiles held, almost any
    neighbour completes one of the four blocks around it, so ten of ten rows were identical."""
    from services.territory import targets_for
    held = {(x, y) for x in range(12) for y in range(12)}
    out = targets_for(acc={}, kept={"A": held}, clan_id="A", won={}, zoom=14)
    flags = [sum((t["joins"], t["blocked"])) for t in out]
    assert max(flags) <= 1, "a row carries at most one claim about what it would do"


def test_ground_you_already_rode_enough_says_it_needs_a_neighbour():
    """A square only counts as part of a 2x2, so a lone line of tiles can be ridden to death
    and still belong to nobody. That is the rule people trip over."""
    from services.territory import targets_for
    acc = {"14/50/50": {"A": [40.0, {"r"}]}}
    out = targets_for(acc, kept={}, clan_id="A", won={}, zoom=14)
    row = next(t for t in out if (t["x"], t["y"]) == (50, 50))
    assert row["need"] == 0.0 and row["blocked"], row


def test_a_crew_holding_nothing_still_gets_told_where_to_go():
    """It was the only crew not being told, which is backwards: it is the one with no idea
    where to start."""
    from services.territory import targets_for
    acc = {"14/50/50": {"A": [40.0, {"r"}]}, "14/51/50": {"A": [12.0, {"r"}]}}
    out = targets_for(acc, kept={"B": {(9, 9), (9, 10), (10, 9), (10, 10)}},
                      clan_id="A", won={}, zoom=14)
    assert {(t["x"], t["y"]) for t in out} == {(50, 50), (51, 50)}


def test_your_own_ground_is_never_offered_back_to_you():
    """Sending a crew to ride a tile it already holds is the one piece of advice that cannot
    possibly help."""
    from services.territory import targets_for
    won = {"14/5/5": ("A", 9.0, 1)}
    acc = {"14/5/5": {"A": [9.0, {"r"}]}}
    out = targets_for(acc, {"A": {(5, 5)}}, "A", won, 14)
    assert all((t["x"], t["y"]) != (5, 5) for t in out)


def test_a_row_never_argues_with_its_own_number():
    """A row that prints a shortfall has to print one you can actually ride off, and a row
    that prints nothing has to mean there is nothing to ride. Rounding to nearest broke both
    ends: 0.04 km short came out as "0.0 km" next to "go ride it", and 0.147 came out as
    "0.1 km" when a 0.1 km contribution to that square is discarded whole."""
    from services.territory import targets_for, min_lead_km, min_visit_km
    import math
    tile = "14/70/70"
    acc = {tile: {"A": [min_lead_km(tile) - 0.04, {"r"}]}}   # short by a sliver
    out = targets_for(acc, kept={}, clan_id="A", won={}, zoom=14)
    row = next(t for t in out if (t["x"], t["y"]) == (70, 70))
    assert row["need"] >= math.ceil(min_visit_km(tile) * 10) / 10, row
    assert not row["blocked"], "short is short, however little"
    acc = {tile: {"A": [min_lead_km(tile) + 1.0, {"r"}]}}    # past the floor, still unheld
    row = next(t for t in targets_for(acc, kept={}, clan_id="A", won={}, zoom=14)
               if (t["x"], t["y"]) == (70, 70))
    assert row["need"] == 0.0 and row["blocked"], row


def test_the_list_can_point_past_the_end_of_your_own_street():
    """Candidates used to be squares touching ground the crew already held, so the biggest ask
    anywhere in the demo world was 1.0 km and the median was 0.3 km. Nobody reroutes a weekend
    for three hundred metres. A rival within riding distance is a reason to go somewhere."""
    from services.territory import targets_for, REACH
    mine = {(x, y) for x in range(2) for y in range(2)}
    rival = {(x, y) for x in range(1 + REACH, 3 + REACH) for y in range(2)}
    out = targets_for(acc={}, kept={"A": mine, "B": rival}, clan_id="A", won={}, zoom=14)
    reached = {(t["x"], t["y"]) for t in out if t["held_by"] == "B"}
    assert reached, "a rival four squares away has to be reachable from the list"
    assert max(t["x"] for t in out) > 2, "and the list has to point past our own edge"


def test_the_roadtrip_between_two_patches_is_a_row_you_can_see():
    """The board ranks on the biggest single patch, so welding two together beats widening
    either end by any amount. For three rounds the flag that says so fired on nothing, ever:
    it needed one square touching two patches at once, which happens only across a gap of
    exactly one, where the square is an ordinary neighbour anyway. A real crew riding two
    parts of a city is two to four squares short, and those squares now carry how many."""
    from services.territory import targets_for
    for gap, want in ((1, True), (2, True), (3, True), (4, True), (6, False)):
        left = {(0, 0), (1, 0), (0, 1), (1, 1)}
        right = {(2 + gap, 0), (3 + gap, 0), (2 + gap, 1), (3 + gap, 1)}
        out = targets_for(acc={}, kept={"A": left | right}, clan_id="A", won={}, zoom=14,
                          limit=60)
        links = [t for t in out if t["links"]]
        assert bool(links) is want, (gap, [(t["x"], t["y"]) for t in links])
        if want:
            assert all(t["links"] == gap for t in links), (gap, links)
            assert all(2 <= t["x"] <= 1 + gap for t in links), links


def test_a_crew_one_square_from_existing_is_told_which_square():
    """Four riders, ten squares led on kilometres, nothing drawn, because a square only counts
    inside a 2x2. They were one square from being on the map and every row said the same
    useless thing."""
    from services.territory import targets_for
    led = {"14/5/5": ("A", 9.0, 1), "14/6/5": ("A", 9.0, 1), "14/5/6": ("A", 9.0, 1)}
    acc = {t: {"A": [9.0, {"r"}]} for t in led}
    acc["14/6/6"] = {"A": [0.0, set()]}
    out = targets_for(acc, kept={}, clan_id="A", won=led, zoom=14)
    assert out and out[0]["first"], out[:2]
    assert (out[0]["x"], out[0]["y"]) == (6, 6), out[0]


def test_no_row_asks_for_a_ride_too_short_to_count():
    """A trip putting less than the visit floor into a square is discarded whole, so a row
    reading 0.1 km where the floor is 0.15 asks for a lap that cannot possibly register."""
    from services.territory import targets_for, min_visit_km
    out = targets_for(acc={}, kept={"A": {(0, 0), (1, 0), (0, 1), (1, 1)}},
                      clan_id="A", won={}, zoom=14)
    for t in out:
        if t["need"] > 0:
            assert t["need"] >= round(min_visit_km(f"14/{t['x']}/{t['y']}"), 1), t


def test_a_row_carries_at_most_one_claim():
    """The first cut marked every row "completes a 2x2": with 90 tiles held, almost any
    neighbour completes one of the four blocks around it, so ten of ten rows were identical."""
    from services.territory import targets_for
    held = {(x, y) for x in range(12) for y in range(12)}
    out = targets_for(acc={}, kept={"A": held}, clan_id="A", won={}, zoom=14)
    assert max(sum((t["first"], t["joins"], t["blocked"])) for t in out) <= 1


def test_a_crew_that_can_take_nothing_sets_off_no_warnings():
    """Half the pressure warnings in the whole world fired on one crew that holds no ground
    anywhere and therefore cannot take a square off anybody. That is exactly the false alarm
    the band rules were written to stop."""
    from services.territory import _pressure
    acc = {"14/1/1": {"holder": [9.0, {"a"}], "ghost": [8.9, {"b"}]}}
    band, _ = _pressure(acc, "14/1/1", "holder", 9.0)
    assert band == 2, "a rival at 99% is about to flip it"
    band, _ = _pressure(acc, "14/1/1", "holder", 9.0, seedless={"ghost"})
    assert band == 0, "unless that rival draws nothing anywhere and never will"


def test_new_ground_rides_along_in_the_band_without_a_sixth_column():
    """The map payload is five integers a tile and every visitor downloads it, so one bit
    saying "taken this week" travels inside the band rather than as a whole extra column."""
    for band in range(5):
        packed = band + 5
        assert packed % 5 == band, "the band survives the fold"
        assert packed >= 5, "and the bit is readable"
    assert 4 % 5 == 4 and 9 % 5 == 4, "ringed ground, fresh and not, decode the same"


def test_a_crew_with_nobody_in_charge_can_still_be_taken_over(db):
    """A leader may walk out the moment an officer exists, which leaves no leader row at all.
    The officer cannot disband (not the leader) and cannot be promoted (only a leader
    promotes), so claim_leadership is the only way out. It used to raise "no leader to
    replace", and once that was guarded at the top of the function it crashed on the write."""
    from datetime import timedelta
    from models import Clan, ClanMember, utcnow
    from services import crews
    db.add(Clan(clan_id="t-nolead", name="No One In Charge", slug="no-one-in-charge",
                colour="#46f0f0", pattern="solid", join_policy="open", invite_code="X"))
    from models import Rider
    db.add(Rider(store_id="t-officer", display_name="Officer", flag="NO"))
    db.commit()                       # the membership points at both of these
    db.add(ClanMember(clan_id="t-nolead", store_id="t-officer", role="officer",
                      status="active",
                      joined_at=utcnow() - timedelta(days=crews.IDLE_LEADER_DAYS + 5),
                      last_seen=utcnow()))
    db.commit()
    crews.claim_leadership(db, "t-officer", "t-nolead")
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == "t-nolead",
                 ClanMember.store_id == "t-officer").first())
    assert m.role == "leader", "the one person left has to be able to take it over"


def test_a_bigger_loop_never_pays_less_than_a_smaller_one():
    """The guard that stops a thin ring swallowing a city used to drop the whole gain, so a
    loop one block wider went from paying double to paying nothing. A rule that punishes
    riding further is the one kind this file is not allowed to have."""
    from services.territory import _close_holes, regions
    last = 0.0
    for n in (5, 6, 7, 8, 10, 14, 20):
        ring = set()
        for i in range(n):
            ring |= {(i, 0), (i, n - 1), (0, i), (n - 1, i)}
        ring |= {(1, 1), (2, 1), (1, 2), (2, 2)}        # a bulge so it seeds
        held = _close_holes({"A": ring})["A"]
        ratio = len(held) / len(ring)
        assert ratio >= last - 1e-9, f"{n}x{n} pays {ratio:.2f}, down from {last:.2f}"
        assert ratio <= 2.0 + 1e-9, f"{n}x{n} pays {ratio:.2f}, a ring must not swallow a city"
        # and it has to stay one piece. Taking the middle of the hole first left a floating
        # disc with a gap between it and the loop that earned it: two regions, a second
        # emblem in the middle of nowhere, and the ranked number unmoved.
        comps = regions(held)
        assert len(comps) == 1, f"{n}x{n} came out as {len(comps)} patches"
        assert max(len(c) for c in comps) == len(held)
        last = ratio


def test_joining_brings_a_fortnight_not_a_season():
    """At the full ninety-day window this was the biggest lever in the game and it needed no
    riding: one free agent joining took the smallest crew from 4 squares to 53, and it is
    one-shot and global, so recruiting a rider denies their backlog to everyone else for
    good. It exists so a crew founded today is not looking at an empty map."""
    from web.crews_api import JOIN_BACKFILL_DAYS
    from services.territory import WINDOW_DAYS
    assert JOIN_BACKFILL_DAYS <= 21, "a joiner must not hand over a whole season"
    assert JOIN_BACKFILL_DAYS < WINDOW_DAYS / 3, (JOIN_BACKFILL_DAYS, WINDOW_DAYS)
    assert JOIN_BACKFILL_DAYS >= 7, "and a crew founded today should still see something"


def test_the_first_crews_each_get_their_own_colour(db):
    """Counting (colour, pattern) pairs alone let two crews share a colour while twenty others
    went unused, and the demo world produced it twice. At the fourteen-pixel swatch the board
    draws, two crews on one colour are the same square whatever pattern is on them."""
    from models import Clan
    from services.crews import PALETTE, suggest_identity
    seen = []
    for i in range(len(PALETTE)):
        got = suggest_identity(db)
        seen.append(got["colour"])
        db.add(Clan(clan_id=f"t-ident-{i}", name=f"Crew {i}", slug=f"crew-{i}",
                    colour=got["colour"], pattern=got["pattern"],
                    join_policy="open", invite_code=f"I{i}"))
        db.commit()
    assert len(set(seen)) == len(PALETTE), f"{len(set(seen))} colours across {len(PALETTE)} crews"
