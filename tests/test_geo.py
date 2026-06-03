from ingest.geo import country_of, cell_id, cells_for


def test_country_norway():
    # Tromsø coordinates from the real sample trips
    assert country_of(69.667261, 18.925417) == "NO"


def test_cell_quantization_groups_nearby_points():
    a = cell_id(69.667, 18.925, 0.1)
    b = cell_id(69.610, 18.951, 0.1)
    assert a == b == "0.1:696:189"


def test_cell_coarse_zoom():
    assert cell_id(69.667, 18.925, 2.0) == "2.0:34:9"


def test_cell_none_for_missing_coords():
    assert cell_id(None, 1.0, 0.1) is None
    assert cells_for(None, None, [0.1, 2.0]) == {}
