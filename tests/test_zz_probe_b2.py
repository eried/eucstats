"""THROWAWAY probe 2 - reviewer B round 4."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from models import Clan, ClanMember, Rider, Trip, utcnow
from services import crews, pairing, settings

_D = dict(enabled=True, zoom=14, window_days=90, seed=2, cooldown_days=7,
          max_members=0, opacity=0.55, creation_open=True)


def _set(db, **o):
    settings.set_crews(db, **{**_D, **o})


@pytest.fixture
def client(db):
    from main import app
    _set(db, enabled=True)
    return TestClient(app)


def _rider(db, sid, lat=None, lon=None):
    db.add(Rider(store_id=sid, display_name=sid.title(), flag="NO"))
    db.commit()
    db.add(Trip(trip_uuid="q-" + sid, rider_store_id=sid, distance_km=5.0,
                validation_status="validated", start_utc=utcnow(), end_utc=utcnow(),
                start_lat=lat, start_lon=lon))
    db.commit()
    return sid


def _sign(client, db, sid):
    client.cookies.clear()
    p = pairing.start(db, purpose="rider")
    pairing.confirm(db, p["code"], sid)
    r = client.get("/api/v1/pair/poll", params={"token": p["token"]})
    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, r.cookies.get(pairing.COOKIE))


def E(r):
    try:
        return json.loads(r.json()["detail"])
    except Exception:
        return r.text[:200]


def test_probe_double_membership_via_undone_decline(client, db):
    """X declines b; b joins Y; X's leader then 'reconsiders'."""
    _set(db, cooldown_days=0)
    a, b, c2 = _rider(db, "a"), _rider(db, "b"), _rider(db, "c")
    _sign(client, db, a)
    client.post("/api/v1/crews", json={"name": "Crew Ex", "join_policy": "approval"})
    _sign(client, db, c2)
    client.post("/api/v1/crews", json={"name": "Crew Why", "join_policy": "open"})
    _sign(client, db, b)
    print("ASK X", client.post("/api/v1/crews/crew-ex/join", json={}).status_code)
    _sign(client, db, a)
    client.post("/api/v1/crews/crew-ex/decide", json={"store_id": b, "accept": False})
    _sign(client, db, b)
    print("JOIN Y", client.post("/api/v1/crews/crew-why/join", json={}).status_code)
    _sign(client, db, a)
    r = client.post("/api/v1/crews/crew-ex/decide", json={"store_id": b, "accept": True})
    print("X RECONSIDERS while b is in Y:", r.status_code, E(r))
    db.expire_all()
    print("B ROWS", [(m.clan_id[:6], m.status, m.left_at is not None)
                     for m in db.query(ClanMember).filter(ClanMember.store_id == b).all()])
    _sign(client, db, b)
    j = client.get("/api/v1/crews/me").json()
    print("B sees crew:", (j.get("crew") or {}).get("name"), "role", j.get("role"))
    print("Crew Ex members:", [x["name"] for x in
                               client.get("/api/v1/crews/crew-ex").json()["roster"]])
    print("Crew Why members:", [x["name"] for x in
                                client.get("/api/v1/crews/crew-why").json()["roster"]])
    r = client.post("/api/v1/crews/leave", json={})
    print("B LEAVE", r.status_code, E(r))
    db.expire_all()
    print("B ROWS after leave", [(m.clan_id[:6], m.status, m.left_at is not None)
                                 for m in db.query(ClanMember).filter(
                                     ClanMember.store_id == b).all()])


def test_probe_remove_a_pending_rider(client, db):
    a, b = _rider(db, "a"), _rider(db, "b")
    _sign(client, db, a)
    client.post("/api/v1/crews", json={"name": "Gatekeepers", "join_policy": "approval"})
    _sign(client, db, b)
    client.post("/api/v1/crews/gatekeepers/join", json={})
    _sign(client, db, a)
    r = client.post("/api/v1/crews/gatekeepers/remove", json={"store_id": b})
    print("REMOVE a pending rider", r.status_code, E(r))
    _sign(client, db, b)
    j = client.get("/api/v1/crews/me").json()
    print("B ME", {k: j.get(k) for k in ("removed_by", "declined_by", "folded")})


def test_probe_officer_can_remove(client, db):
    a, b, c2 = _rider(db, "a"), _rider(db, "b"), _rider(db, "c")
    _sign(client, db, a)
    client.post("/api/v1/crews", json={"name": "Night Owls", "join_policy": "open"})
    for sid in (b, c2):
        _sign(client, db, sid)
        client.post("/api/v1/crews/night-owls/join", json={})
    _sign(client, db, a)
    client.post("/api/v1/crews/night-owls/role", json={"store_id": b, "role": "officer"})
    _sign(client, db, b)
    j = client.get("/api/v1/crews/me").json()
    print("OFFICER sees roster?", "roster" in j, "len", len(j.get("roster") or []))
    r = client.post("/api/v1/crews/night-owls/remove", json={"store_id": c2})
    print("OFFICER removes member", r.status_code, E(r))
    r = client.post("/api/v1/crews/night-owls/remove", json={"store_id": a})
    print("OFFICER removes leader", r.status_code, E(r))


def test_probe_remove_then_cooldown(client, db):
    _set(db, cooldown_days=7)
    a, b, c2 = _rider(db, "a"), _rider(db, "b"), _rider(db, "c")
    _sign(client, db, a)
    client.post("/api/v1/crews", json={"name": "Night Owls", "join_policy": "open"})
    for sid in (b, c2):
        _sign(client, db, sid)
        client.post("/api/v1/crews/night-owls/join", json={})
    _sign(client, db, a)
    client.post("/api/v1/crews/night-owls/remove", json={"store_id": b})
    _sign(client, db, b)
    j = client.get("/api/v1/crews/me").json()
    print("B after removal", {k: j.get(k) for k in ("removed_by", "cooldown_until")})
    r = client.post("/api/v1/crews/night-owls/join", json={})
    print("B rejoins the crew that kicked them:", r.status_code, E(r))


def test_probe_identity_near(client, db):
    from services import territory, tiles as T
    from models import ClanCell
    a = _rider(db, "a", lat=59.91, lon=10.75)
    b = _rider(db, "b", lat=59.92, lon=10.76)
    _sign(client, db, a)
    r = client.post("/api/v1/crews", json={"name": "Oslo One", "colour": "#e6194b",
                                           "pattern": "solid", "join_policy": "open"})
    print("CREATE", r.status_code, r.text[:120])
    cid = db.query(Clan).first().clan_id
    tile = T.tile_of(59.915, 10.752, 14)
    db.add(ClanCell(tile=tile, clan_id=cid, km=1.0, riders=1, first_led=utcnow()))
    db.commit()
    print("neighbour colours:", crews.neighbour_colours(db, 59.92, 10.76))
    _sign(client, db, b)
    hues = set()
    for _ in range(12):
        s = crews.suggest_identity(db, near=(59.92, 10.76))
        hues.add(s["colour"])
    print("suggested near Oslo (12 draws):", sorted(hues))
    print("gap to #e6194b:", sorted(round(crews._hue_gap(h, "#e6194b")) for h in hues))
    r = client.get("/api/v1/crews/identity")
    print("endpoint:", r.status_code, r.json())


def test_probe_identity_near_crowded(client, db):
    """Every palette colour on the ground nearby: does the founder still get something?"""
    from services import tiles as T
    from models import ClanCell
    _set(db, cooldown_days=0)
    riders = []
    for i, col in enumerate(crews.PALETTE):
        sid = _rider(db, "r%d" % i, lat=59.9, lon=10.7)
        riders.append(sid)
        c = Clan(clan_id="c%02d" % i, name="Crew %02d" % i, slug="crew-%02d" % i,
                 colour=col, pattern="solid", join_policy="open", created_by=sid,
                 invite_code="X%07d" % i)
        db.add(c)
        db.add(ClanMember(clan_id=c.clan_id, store_id=sid, role="leader", status="active",
                          last_seen=utcnow()))
        db.add(ClanCell(tile=T.tile_of(59.9 + i * 0.001, 10.7, 14), clan_id=c.clan_id,
                        km=1.0, riders=1, first_led=utcnow()))
    db.commit()
    print("neighbours:", len(crews.neighbour_colours(db, 59.9, 10.7)))
    s = crews.suggest_identity(db, near=(59.9, 10.7))
    print("still suggests:", s)


def test_probe_pair_errors(client, db):
    _rider(db, "a")
    r = client.get("/api/v1/pair/describe", params={"code": "ZZZZZZ"})
    print("describe bad code:", r.status_code, r.text[:160])
    r = client.post("/api/v1/pair/confirm", json={"code": "ZZZZZZ", "store_id": "a"})
    print("confirm bad code:", r.status_code, r.text[:160])
    r = client.get("/api/v1/pair/poll", params={"token": "nope"})
    print("poll bad token:", r.status_code, r.text[:160])
    p = pairing.start(db, purpose="rider")
    pt = db.query(__import__("models").PairToken).filter_by(token=p["token"]).first()
    pt.created_at = utcnow() - timedelta(minutes=5)
    db.commit()
    r = client.get("/api/v1/pair/poll", params={"token": p["token"]})
    print("poll expired:", r.status_code, r.text[:160])
    r = client.post("/api/v1/pair/confirm", json={"code": p["code"], "store_id": "nosuch"})
    print("confirm unknown rider:", r.status_code, r.text[:160])
    p2 = pairing.start(db, purpose="admin")
    print("admin describe:", client.get("/api/v1/pair/describe",
                                        params={"code": p2["code"]}).json())
    pairing.confirm(db, p2["code"], "a")
    r = client.get("/api/v1/pair/poll", params={"token": p2["token"]})
    print("admin poll 1:", r.status_code, r.json())
    r = client.get("/api/v1/pair/poll", params={"token": p2["token"]})
    print("admin poll 2 (replay):", r.status_code, r.text[:120])


def test_probe_expired_session_midflow(client, db):
    a = _rider(db, "a")
    _sign(client, db, a)
    print("ME paired:", client.get("/api/v1/crews/me").json().get("paired"))
    from models import WebSession
    ws = db.query(WebSession).first()
    ws.last_used = utcnow() - timedelta(days=200)
    db.commit()
    r = client.post("/api/v1/crews", json={"name": "Too Late"})
    print("CREATE on dead session:", r.status_code, r.text[:140])
    print("ME:", client.get("/api/v1/crews/me").json())


def test_probe_targets_face_has_no_place(client, db):
    """face() keys on t['at'], which _name_targets only adds after targets_for returns."""
    import inspect
    from services import territory
    src = inspect.getsource(territory.targets_for)
    print("face() reads 'at':", "t.get(\"at\")" in src)
    acc, kept, won = {}, {"me": set()}, {}
    # two squares, same effort, same holder, far apart
    for (x, y) in ((1000, 500), (1001, 500), (1000, 501), (1001, 501)):
        kept["me"].add((x, y))
    kept["them"] = {(1002, 500), (1002, 501), (1003, 500), (1003, 501)}
    for (x, y) in kept["them"]:
        won["14/%d/%d" % (x, y)] = ("them", 2.0, 1)
    rows = territory.targets_for(acc, kept, "me", won, 14, limit=8)
    print("rows:", [(r["x"], r["y"], r["need"], r["held_by"], r.get("at")) for r in rows])


def test_probe_losable_fresh_has_no_place(client, db):
    import inspect
    from services import territory
    src = inspect.getsource(territory.rebuild)
    i_bump = src.find("band += 5")
    i_los = src.find("losable.append")
    print("band += 5 at", i_bump, "losable.append at", i_los,
          "-> losable computed after the bump:", i_los > i_bump)
