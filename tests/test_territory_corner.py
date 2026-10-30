"""Two blobs that meet at a point, and the one thing that decides whether they are one.

Erwin, after three days with the bridge rule that preceded this one: "the thing is I want this
to be always connected by this logic when is in this way, unless another takes a square of the
ones we need to connect them, we just need some simple rule when 2 blobs touch in the corner,
they act connected, but they dont if there is another rival crew square next because then it
means they can actually take that square back to connect... in the 2x2 connection point if 1
and square 4 are crew A we assume they are connected because 2 and 3 squares arent taken
(maybe is not possible, no road, etc) but if square 2 is taken by crew B... means crew A could
actually take it, so we dont connect them by the corner in this case".

The rule that replaced it is that paragraph: four squares meet at a point, a crew holds the
two on one diagonal, and the gap is the other two. Empty gap, one patch, because an empty gap
may be a building, a fjord or a field with no road through it, and the rider sees one piece of
ground. A rival in either gap square and they stay apart, because a rival there is proof the
gap can be ridden, and the way to join them is to go and take it.

What this file is really guarding is the two properties the rule was chosen FOR, which the
award it replaced did not have:

  * No square is invented. The gap stays unowned, nobody's count goes up, and the thing that
    changes is only how the patches are counted.
  * Nothing is stored. The answer is read off the current map every rebuild, so territory
    stays a pure function of the last ninety days of riding and two crews that rode the same
    ground hold the same ground.
"""
from services import territory as TR

Z = 14


def tile(x, y):
    return f"{Z}/{x}/{y}"


def block(x0, y0, n=2):
    return {(x0 + dx, y0 + dy) for dx in range(n) for dy in range(n)}


def rode(acc, xy, clan, part, riders=1):
    """Put `part` of a lead's worth of riding in a square. 1.0 is exactly the lead floor.

    Scaled to the tile rather than written as kilometres, because every floor in this file is
    a fraction of a tile's own edge and a flat number would mean something different at every
    latitude.
    """
    t = tile(*xy)
    acc.setdefault(t, {})[clan] = [TR.min_lead_km(t) * part, {f"r{n}" for n in range(riders)}]
    return acc


# Two 2x2 blocks meeting at one point: (1,1) and (2,2) are diagonal neighbours, so (2,1) and
# (1,2) are the gap. This is the shape Erwin numbered 1 and 4, with 2 and 3 between them.
WEST, EAST = block(0, 0), block(2, 2)
BOTH = WEST | EAST
GAP = {(2, 1), (1, 2)}


def test_an_empty_gap_makes_the_two_blobs_one_patch():
    assert len(TR.regions(BOTH, set())) == 1
    assert sorted(len(p) for p in TR.regions(BOTH, set())) == [8]


def test_a_rival_in_the_gap_keeps_them_apart():
    """A rival there is evidence of a route, so the split is real ground rather than an
    artefact of where the grid fell, and it can be ridden for."""
    assert len(TR.regions(BOTH, {(2, 1)})) == 2
    assert len(TR.regions(BOTH, {(1, 2)})) == 2, "either square is enough, not just the one"
    assert len(TR.regions(BOTH, set(GAP))) == 2


def test_a_rival_beside_the_corner_but_not_in_the_gap_changes_nothing():
    """Only the two squares of that corner's own gap can speak for it. A rival one square
    further out is a neighbour, which is the ordinary state of a map."""
    for outside in ((3, 1), (1, 3), (0, 2), (2, 0), (4, 4), (-1, -1)):
        assert len(TR.regions(BOTH, {outside})) == 1, f"{outside} is not in this gap"


def test_without_the_rest_of_the_map_the_answer_is_edge_only():
    """`rivals=None` is "the caller does not know who holds what", and the honest answer
    there is the old one. Every caller that ranks, draws or quotes a patch passes the map;
    this is what the rest get."""
    assert len(TR.regions(BOTH)) == 2
    assert len(TR.seeded(BOTH | {(4, 4)})) == 8, "the lone square is dropped, as before"


def test_a_seed_block_reaches_an_outpost_across_an_empty_corner():
    """`seeded` is the rule that turns scattered squares into territory, and it has to agree
    with the counting or a crew would be ranked on a patch the map does not draw."""
    held = WEST | {(2, 2)}
    assert TR.seeded(held, rivals=set()) == held
    assert TR.seeded(held, rivals={(2, 1)}) == WEST, (
        "with a rival in the gap the outpost is a lone square again, and a lone square has "
        "no 2x2 of its own")
    assert TR.seeded(held) == WEST, "and without the map, the old answer"


def test_a_staircase_of_blocks_is_one_patch_and_still_costs_what_it_costs():
    """The shape to check for farming: 2x2 blocks on a diagonal, each meeting the next at a
    point. Under this rule they are one patch, which is the point of the rule.

    It buys nothing it has not paid for. Every square in it still had to be claimed, no gap
    square becomes anybody's, and the count is exactly the squares ridden -- which is what
    makes this cheaper for us than the award it replaced, where each corner also handed over
    a square for a fifth of a claim.
    """
    chain = set()
    for k in range(5):
        chain |= block(2 * k, 2 * k)
    assert len(chain) == 20
    assert len(TR.regions(chain, set())) == 1
    assert len(TR.regions(chain)) == 5, "five blocks, five corners, nothing connected before"
    assert TR.seeded(chain, rivals=set()) == chain
    assert len(chain) == 20, "no square was added to it by being connected"


def test_the_whole_pipeline_turns_two_patches_into_one_and_awards_nothing():
    """Through `award()`, where it actually runs: the lead contest, seeding and the hole rule
    in the order a rebuild uses them."""
    acc = {}
    for xy in BOTH:
        rode(acc, xy, "a", 1.4)                  # comfortably a claim
    rode(acc, (2, 1), "a", 0.5)                  # and a visit in the gap, as the road left it
    kept, _won, _blocked = TR.award(acc, {"a"}, {})
    assert kept["a"] == BOTH, "the gap square belongs to nobody, and that is the whole idea"
    assert len(TR.regions(kept["a"], set())) == 1
    assert (2, 1) not in kept["a"]


def test_a_rival_holding_the_gap_survives_the_pipeline_as_two_patches():
    acc = {}
    for xy in BOTH:
        rode(acc, xy, "a", 1.4)
    rode(acc, (2, 1), "b", 1.4)                  # the rival claims one of the gap squares
    rode(acc, (3, 1), "b", 1.4)                  # and enough around it to be drawable at all
    rode(acc, (2, 0), "b", 1.4)
    rode(acc, (3, 0), "b", 1.4)
    kept, _won, _blocked = TR.award(acc, {"a", "b"}, {})
    assert (2, 1) in kept["b"]
    mine = kept["a"]
    assert len(TR.regions(mine, TR._others(kept, "a", mine))) == 2, (
        "the rival is in the gap, so these are two patches and the way to join them is to "
        "ride that square")


def test_taking_the_square_back_joins_them_again():
    """The move the rule is pointing at. Same world as above, with the crew out-riding the
    rival in the gap square instead of leaving it to them."""
    acc = {}
    for xy in BOTH:
        rode(acc, xy, "a", 1.4)
    rode(acc, (2, 1), "b", 1.4)
    acc[tile(2, 1)]["a"] = [TR.min_lead_km(tile(2, 1)) * 2.0, {"r0"}]   # ridden harder
    kept, _won, _blocked = TR.award(acc, {"a", "b"}, {})
    mine = kept["a"]
    assert (2, 1) in mine, "they out-rode the rival there, so it is theirs by the ordinary rule"
    assert len(TR.regions(mine, TR._others(kept, "a", mine))) == 1


def test_the_same_data_draws_the_same_map():
    """Two rebuilds of one world have to agree, or a crew's rank moves while nobody rides."""
    acc = {}
    for xy in BOTH:
        rode(acc, xy, "a", 1.4)
    rode(acc, (2, 1), "a", 0.5)
    first = TR.award(acc, {"a"}, {})[0]
    for _ in range(4):
        assert TR.award(acc, {"a"}, {})[0] == first


def test_an_enclosed_hole_still_needs_a_ring_that_actually_closes():
    """The corner rule does not quietly turn a diagonal chain into a wall. `fill_enclosed`
    floods between edge neighbours, so a ring that only meets itself at points leaks and
    swallows nothing -- which is the conservative answer and the one that keeps the enclosure
    cap honest."""
    ring = set()
    for k in range(4):
        ring |= block(2 * k, 2 * k)
    assert TR.fill_enclosed(ring, set()) == set(), "it returns the gain, and there is none"
