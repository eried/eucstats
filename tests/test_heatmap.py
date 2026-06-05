"""Heatmap: per-dataset settings, whole-route cell spreading, privacy floor."""
from datetime import datetime

import models
from ingest.downsample import encode_track
from ingest.parser import Sample
from services import settings, stats
from services.aggregator import Aggregator


def test_heatmap_settings_roundtrip(db):
    hm = settings.get_heatmap(db)
    assert hm["cell_size"] == 0.025 and hm["route_mode"] == "route" and hm["floor"] == 2
    settings.set_heatmap(db, cell_size=0.05, route_mode="start", floor=3,
                         radius=80, intensity=2.0, opacity=0.5)
    hm = settings.get_heatmap(db)
    assert hm["cell_size"] == 0.05 and hm["route_mode"] == "start" and hm["floor"] == 3
    assert settings.heatmap_zooms(db)[-1] == 0.05      # finest tier follows cell_size


def _trip_with_track(db, sid, uuid, pts):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play"))
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=sid, validation_status="validated",
                       distance_km=5.0, start_lat=pts[0][0], start_lon=pts[0][1]))
    db.commit()
    samples = [Sample(t=datetime(2026, 6, 1, 10, 0, i), lat=la, lon=lo, speed=20.0)
               for i, (la, lo) in enumerate(pts)]
    db.add(models.TripTrack(trip_uuid=uuid, points=encode_track(samples)))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, uuid))


def test_route_mode_spreads_across_cells(db):
    # a route crossing several 0.025-degree cells should light up several cells
    settings.set_heatmap(db, 0.025, "route", 1, 60, 1.0, 0.62)   # floor 1 so all show
    pts = [(69.60 + i * 0.03, 18.90) for i in range(5)]          # ~5 distinct lat cells
    _trip_with_track(db, "r1", "t1", pts)
    cells = stats.map_cells(db, 0.025)
    assert len(cells) >= 4                                       # multiple cells, not one


def test_start_mode_single_cell(db):
    settings.set_heatmap(db, 0.025, "start", 1, 60, 1.0, 0.62)
    pts = [(69.60 + i * 0.03, 18.90) for i in range(5)]
    _trip_with_track(db, "r2", "t2", pts)
    assert len(stats.map_cells(db, 0.025)) == 1                  # only the start cell


def test_rebuild_preserves_rider_count(db):
    # regression: rebuild_all must clear MapCellRider too, else rebuilt cells come back
    # with rider_count=0 and the heatmap silently empties (privacy floor hides everything).
    from services.aggregator import rebuild_all
    settings.set_heatmap(db, 0.025, "start", 1, 60, 1.0, 0.62)   # floor 1
    _trip_with_track(db, "rr", "trr", [(69.60, 18.90)])
    assert len(stats.map_cells(db, 0.025)) == 1                  # shows before rebuild
    rebuild_all(db)
    db.expire_all()
    cells = stats.map_cells(db, 0.025)
    assert len(cells) == 1 and cells[0]["rider_count"] == 1      # still shows, count intact


def test_privacy_floor_hides_low_rider_cells(db):
    settings.set_heatmap(db, 0.025, "start", 2, 60, 1.0, 0.62)   # floor 2
    _trip_with_track(db, "a", "ta", [(60.0, 10.0)])             # 1 rider in this cell
    assert stats.map_cells(db, 0.025) == []                      # hidden (only 1 rider)
    _trip_with_track(db, "b", "tb", [(60.0, 10.0)])             # 2nd distinct rider, same cell
    assert len(stats.map_cells(db, 0.025)) == 1                  # now it shows
