"""Mercator tiling — the grid territory is drawn on."""
from services import tiles


def test_a_tile_is_square_on_screen_at_every_latitude():
    """The reason for not reusing the heatmap's degree grid. A degree cell in Oslo is far
    taller than it is wide, so a crew emblem dropped into one would be squashed; a Mercator
    tile is square wherever you are, which is what makes an emblem placeable at all."""
    for lat in (0.0, 35.0, 59.91, 70.0):
        w, s, e, n = tiles.bounds(tiles.tile_of(lat, 10.0))
        import math
        width_km = (e - w) / 360.0 * tiles.EARTH_C_KM * math.cos(math.radians((s + n) / 2))
        height_km = (n - s) * 111.32
        assert 0.92 < width_km / height_km < 1.08, f"not square at {lat}: {width_km}x{height_km}"


def test_ground_size_shrinks_toward_the_poles():
    """Northern riders get finer tiles for free, which is why a 2x2 seed is an ordinary ride
    in Oslo. Ranking is therefore in km rather than tile count."""
    oslo = tiles.area_km2(tiles.tile_of(59.91, 10.75))
    equator = tiles.area_km2(tiles.tile_of(0.0, 10.75))
    assert oslo < equator / 3, (oslo, equator)
    assert 4.0 < oslo < 8.0, oslo            # ~2.4 km a side
    assert 20.0 < equator < 28.0, equator    # ~4.9 km a side


def test_a_fast_ride_leaves_no_holes():
    """Consecutive fixes can be hundreds of metres apart. Mapping each fix to a tile and
    stopping there would leave gaps in the middle of a ride, so the line between them is
    walked and the tiles underneath filled in."""
    route = [(59.90, 10.70), (59.90, 10.90)]          # ~11 km apart, one hop
    got = tiles.tiles_along(route)
    assert len(got) > 3, got
    for a, b in zip(got, got[1:]):
        assert b in tiles.neighbours(a), f"hole between {a} and {b}"


def test_neighbours_are_edges_not_corners():
    """Territory grows through edges. Two blocks touching at a single point are not one
    region to anyone looking at the map."""
    n = tiles.neighbours("13/4300/2400")
    assert set(n) == {"13/4301/2400", "13/4299/2400", "13/4300/2401", "13/4300/2399"}


def test_bad_input_is_dropped_not_guessed():
    assert tiles.tile_of(None, 10.0) is None
    assert tiles.tile_of(59.9, None) is None
    assert tiles.tile_of(59.9, 999.0) is None
    assert tiles.tile_of(89.9, 10.0) is not None     # clamped, not rejected
    assert tiles.parse("nonsense") is None
