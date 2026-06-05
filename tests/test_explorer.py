"""Admin explorer pages render, and ban/unban work end-to-end through the UI."""
import json
from datetime import datetime

import pyotp
from fastapi.testclient import TestClient

import config
import models
from main import app
from repository.riders import RiderRepo
from repository.trips import TripRepo
from services import settings, stats


def _seed(db):
    RiderRepo(db).upsert("ex1", "google_play", "Explorer Ed", "NO")
    db.add(models.Wheel(wheel_id="w1", rider_store_id="ex1", brand="Begode", model="Master"))
    TripRepo(db).insert_trip(trip_uuid="tr1", rider_store_id="ex1", distance_km=12.0,
                             start_utc=datetime(2026, 6, 1), country="NO",
                             start_lat=69.6, start_lon=18.9, max_speed=45.0,
                             validation_status="validated", max_freespin=88.0,
                             max_voltage_sag=9.5, sustained_accel=12.0,
                             meta_json={"max_gforce_spike": 6.2})
    db.commit()


def _auth(client):
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    client.get("/admin")
    secret = json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]
    client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})


def test_explorer_pages_render(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.get("/admin/explorer")
        assert r.status_code == 200 and "Explorer Ed" in r.text
        assert client.get("/admin/explorer?q=ed").status_code == 200
        rd = client.get("/admin/explorer/rider/ex1")
        assert rd.status_code == 200
        assert "Begode" in rd.text and "Ban rider" in rd.text and "Master" in rd.text
        assert client.get("/admin/explorer/trips").status_code == 200
        td = client.get("/admin/explorer/trip/tr1")
        assert td.status_code == 200
        assert "Metrics" in td.text and "freespin spike" in td.text   # meta_json freespin surfaced
        assert 'id=tmap' in td.text                                   # route map present (has start coords)
        assert client.get("/admin/explorer/rider/nope").status_code == 404
        assert client.get("/admin/explorer/trip/nope").status_code == 404


def test_trip_track_geojson(db):
    from datetime import datetime
    from ingest.downsample import encode_track
    from ingest.parser import Sample
    from repository.trips import TripRepo
    _seed(db)
    pts = [Sample(t=datetime(2026, 6, 1, 10, 0, i), lat=69.6 + i * 0.001,
                  lon=18.9 + i * 0.001, speed=20.0, g=1.0) for i in range(5)]
    TripRepo(db).save_track("tr1", encode_track(pts))
    db.commit()
    with TestClient(app) as client:
        _auth(client)
        r = client.get("/admin/explorer/trip/tr1/track.geojson")
        assert r.status_code == 200
        g = r.json()
        roles = {f["properties"]["role"] for f in g["features"]}
        assert roles == {"path", "start", "end"}
        line = next(f for f in g["features"] if f["properties"]["role"] == "path")
        assert len(line["geometry"]["coordinates"]) == 5
        assert line["geometry"]["coordinates"][0] == [18.9, 69.6]   # [lon, lat] order


def test_trip_track_requires_auth(db):
    _seed(db)
    with TestClient(app) as client:
        assert client.get("/admin/explorer/trip/tr1/track.geojson").status_code == 401


def test_metrics_tree_shows_descriptions(db):
    with TestClient(app) as client:
        _auth(client)
        r = client.get("/admin/metrics")
        assert r.status_code == 200
        assert "Mile Muncher" in r.text and "Most distance ever ridden" in r.text
        assert "class=mnode" in r.text and 'data-parent="riders"' in r.text   # nested tree
        assert 'data-parent="records"' in r.text and "Top Speed" in r.text    # records have children now
        assert "Freespin King" in r.text and "Sag Lord" in r.text and "Rocket" in r.text   # new boards hideable


def test_records_visibility_save(db):
    with TestClient(app) as client:
        _auth(client)
        data = {
            "show_section": [k for k, *_ in settings.METRIC_SECTIONS],
            "show_board": [k for k, *_ in settings.METRIC_BOARDS],
            "show_group": [k for k, *_ in settings.METRIC_GROUPS],
            "show_app": [k for k, *_ in settings.METRIC_APP],
            "show_record": [k for k, *_ in settings.METRIC_RECORDS if k != "top_speed"],
        }
        r = client.post("/admin/metrics/save", data=data, follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert "top_speed" in settings.get_hidden(db)["records"]


def test_system_and_pipeline_pages(db):
    with TestClient(app) as client:
        _auth(client)
        sp = client.get("/admin/system")
        assert sp.status_code == 200 and "Data retention" in sp.text and "Server resources" in sp.text
        pp = client.get("/admin/pipeline")
        assert pp.status_code == 200 and "Anti-fraud rules" in pp.text and "Thresholds" in pp.text


def test_pipeline_rules_save(db):
    with TestClient(app) as client:
        _auth(client)
        r = client.post("/admin/pipeline/rules",
                        data={"rule": ["mock_location", "impossible_speed"], "thr_max_kmh": "90"},
                        follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        dis = settings.pipeline_disabled(db)
        assert "teleport" in dis and "mock_location" not in dis
        assert settings.get_thresholds(db)["max_kmh"] == 90.0


def test_system_save(db):
    with TestClient(app) as client:
        _auth(client)
        r = client.post("/admin/system/save",
                        data={"ret_days": "14", "ret_floor_gb": "5.5", "ret_interval_s": "600"},
                        follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        r2 = settings.get_retention(db)
        assert r2["days"] == 14 and r2["disk_floor_gb"] == 5.5 and r2["interval_s"] == 600


def test_ban_and_unban_through_ui(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.post("/admin/rider/ex1/ban", data={"reason": "GPS spoofing"},
                        follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert settings.is_banned(db, "ex1") is True
        assert stats.rider_card(db, "ex1")["ban_reason"] == "GPS spoofing"
        page = client.get("/admin/explorer/rider/ex1")
        assert "Account suspended" in page.text

        r = client.post("/admin/rider/ex1/unban", follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert settings.is_banned(db, "ex1") is False
