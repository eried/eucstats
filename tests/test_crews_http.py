"""The crew API over HTTP, which nothing tested at all.

Both earlier crew suites stop at the service boundary, so everything that only exists in the
route layer was unverified: the gate's 404 and the exact `crews_disabled` string the client
string-matches on, the 401, the `{"code": ..., "detail": ...}` envelope the whole client error
map is built on, the member cap, the creation switch, and the `/crews/me` payload the panel
renders from. A reviewer pointed out that the fix for the take-over button was tested as a
service function and never as the field the browser actually reads, which is the half that
can break without a single test going red.
"""
import json

import pytest
from fastapi.testclient import TestClient

from models import Clan, ClanMember, Rider, Trip, utcnow
from services import crews, pairing, settings


# Written out rather than read back from get_crews: the config is cached in the process and
# the per-test schema reset does not clear that cache, so basing one test's settings on
# whatever the last one left behind made them pass alone and fail together.
_CREW_DEFAULTS = dict(enabled=True, zoom=14, window_days=90, seed=2, cooldown_days=7,
                      max_members=0, opacity=0.55, creation_open=True)


def _set_crews(db, **over):
    settings.set_crews(db, **{**_CREW_DEFAULTS, **over})


@pytest.fixture
def client(db):
    """Crews are off on a fresh install, which is the right default and the wrong starting
    point for a suite about them."""
    from main import app
    _set_crews(db, enabled=True)
    return TestClient(app)


def _rider(db, sid, name=None):
    db.add(Rider(store_id=sid, display_name=name or sid.title(), flag="NO"))
    db.commit()
    db.add(Trip(trip_uuid=f"h-{sid}", rider_store_id=sid, distance_km=5.0,
                validation_status="validated", start_utc=utcnow(), end_utc=utcnow()))
    db.commit()
    return sid


def _signed_in(client, db, sid):
    """A real pass, minted the way a phone mints one.

    The jar is cleared first: httpx keeps one cookie per (name, domain, path) and the poll
    response sets its own, so signing a second rider in without this leaves two and every
    later read raises CookieConflict.
    """
    client.cookies.clear()
    p = pairing.start(db, purpose="rider")
    pairing.confirm(db, p["code"], sid)
    r = client.get("/api/v1/pair/poll", params={"token": p["token"]})
    assert r.status_code == 200, r.text
    sess = r.cookies.get(pairing.COOKIE)
    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, sess)
    return sess


def _err(resp) -> dict:
    """The envelope the client unpacks: a JSON object inside HTTPException.detail."""
    return json.loads(resp.json()["detail"])


# --- the gate ---------------------------------------------------------------------------

def test_crews_switched_off_answers_with_the_code_the_panel_matches_on(client, db):
    """`crews.js` compares the detail string to the literal "crews_disabled" to decide
    whether to show "Crews are off" instead of a sign-in form. Nothing checked that the
    server still says that word."""
    _set_crews(db, enabled=False)
    r = client.get("/api/v1/crews/me")
    assert r.status_code == 404
    assert r.json()["detail"] == "crews_disabled"


def test_a_write_without_a_pass_is_a_401(client, db):
    r = client.post("/api/v1/crews", json={"name": "No Pass"})
    assert r.status_code == 401
    assert r.json()["detail"] == "not_paired"


# --- founding ---------------------------------------------------------------------------

def test_founding_over_http_returns_the_crew_and_the_errors_are_envelopes(client, db):
    _rider(db, "h1")
    _signed_in(client, db, "h1")
    r = client.post("/api/v1/crews", json={"name": "Over The Wire", "join_policy": "open"})
    assert r.status_code == 200, r.text
    assert r.json()["crew"]["name"] == "Over The Wire"

    _rider(db, "h2")
    _signed_in(client, db, "h2")
    r = client.post("/api/v1/crews", json={"name": "Over The Wire"})
    assert r.status_code == 400
    assert _err(r)["code"] == "name_taken"


def test_a_folded_crews_name_can_be_used_again_over_http(client, db):
    """The path that used to raise an uncaught IntegrityError and reach the browser as
    "That didn't work"."""
    _rider(db, "f1")
    _signed_in(client, db, "f1")
    client.post("/api/v1/crews", json={"name": "Twice Over", "join_policy": "open"})
    slug = db.query(Clan).filter(Clan.name == "Twice Over").one().slug
    assert client.post(f"/api/v1/crews/{slug}/disband").status_code == 200

    _rider(db, "f2")
    _signed_in(client, db, "f2")
    r = client.post("/api/v1/crews", json={"name": "Twice Over"})
    assert r.status_code == 200, r.text


def test_renaming_onto_a_folded_name_is_a_message_not_a_500(client, db):
    _rider(db, "r1")
    _signed_in(client, db, "r1")
    client.post("/api/v1/crews", json={"name": "Gone Already", "join_policy": "open"})
    gone = db.query(Clan).filter(Clan.name == "Gone Already").one()
    client.post(f"/api/v1/crews/{gone.slug}/disband")

    _rider(db, "r2")
    _signed_in(client, db, "r2")
    client.post("/api/v1/crews", json={"name": "Still Here", "join_policy": "open"})
    here = db.query(Clan).filter(Clan.name == "Still Here").one()
    r = client.post(f"/api/v1/crews/{here.slug}/edit", json={"name": "Gone Already"})
    assert r.status_code != 500, "a name clash is an answer, not a crash"


def test_creation_can_be_switched_off(client, db):
    _set_crews(db, creation_open=False)
    _rider(db, "c1")
    _signed_in(client, db, "c1")
    r = client.post("/api/v1/crews", json={"name": "Too Late"})
    assert r.status_code == 403
    assert r.json()["detail"] == "creation_closed"


# --- joining ----------------------------------------------------------------------------

def test_a_full_crew_refuses_and_says_which_rule_it_is(client, db):
    _set_crews(db, max_members=1)
    _rider(db, "m1")
    _signed_in(client, db, "m1")
    client.post("/api/v1/crews", json={"name": "Room For One", "join_policy": "open"})
    slug = db.query(Clan).filter(Clan.name == "Room For One").one().slug

    _rider(db, "m2")
    _signed_in(client, db, "m2")
    r = client.post(f"/api/v1/crews/{slug}/join", json={})
    assert r.status_code == 403
    assert r.json()["detail"] == "crew_full"


def test_the_listing_carries_the_cap_so_a_row_can_say_full(client, db):
    r = client.get("/api/v1/crews")
    assert r.status_code == 200
    assert "max_members" in r.json(), "the join list cannot mark a crew full without this"


# --- /crews/me, which the panel renders from --------------------------------------------

def test_me_tells_the_panel_who_may_take_a_quiet_crew_over(client, db):
    """`can_claim` is the field the take-over button is gated on. It was fixed as a service
    function and never checked as the thing the browser reads."""
    from datetime import timedelta
    _rider(db, "q1")
    _signed_in(client, db, "q1")
    client.post("/api/v1/crews", json={"name": "Quiet Ones", "join_policy": "open"})
    clan = db.query(Clan).filter(Clan.name == "Quiet Ones").one()

    _rider(db, "q2")
    _signed_in(client, db, "q2")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})

    lead = (db.query(ClanMember)
            .filter(ClanMember.clan_id == clan.clan_id, ClanMember.role == "leader").one())
    lead.last_seen = utcnow() - timedelta(days=crews.IDLE_LEADER_DAYS + 3)
    db.commit()

    me = client.get("/api/v1/crews/me").json()
    assert me["leader_stale"] is True
    assert me["can_claim"] is True

    _rider(db, "q3")
    _signed_in(client, db, "q3")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})
    me3 = client.get("/api/v1/crews/me").json()
    assert me3.get("can_claim") is False, "only the longest-serving member is offered it"


def test_a_declined_rider_is_told_once(client, db):
    """Accepted or refused, a request used to have no answer at all: the panel just went back
    to the join list, which is also what cancelling your own request looks like."""
    _rider(db, "d1")
    _sess = _signed_in(client, db, "d1")
    client.post("/api/v1/crews", json={"name": "Picky", "join_policy": "approval"})
    clan = db.query(Clan).filter(Clan.name == "Picky").one()
    leader = _sess

    _rider(db, "d2")
    _sess = _signed_in(client, db, "d2")
    assert client.post(f"/api/v1/crews/{clan.slug}/join", json={}).status_code == 200
    asker = _sess

    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, leader)
    r = client.post(f"/api/v1/crews/{clan.slug}/decide",
                    json={"store_id": "d2", "accept": False})
    assert r.status_code == 200, r.text

    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, asker)
    first = client.get("/api/v1/crews/me").json()
    assert first.get("declined_by") == "Picky"
    again = client.get("/api/v1/crews/me").json()
    assert again.get("declined_by") is None, "news once, not for ever"


def test_being_turned_down_costs_no_cooldown(client, db):
    _rider(db, "n1")
    _sess = _signed_in(client, db, "n1")
    client.post("/api/v1/crews", json={"name": "No Thanks", "join_policy": "approval"})
    clan = db.query(Clan).filter(Clan.name == "No Thanks").one()
    leader = _sess

    _rider(db, "n2")
    _sess = _signed_in(client, db, "n2")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})

    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, leader)
    client.post(f"/api/v1/crews/{clan.slug}/decide", json={"store_id": "n2", "accept": False})
    assert crews.cooldown_until(db, "n2") is None, (
        "a request that was never accepted is not a crew you walked out of")


# --- the redraw clock --------------------------------------------------------------------

def test_the_panel_is_told_how_long_between_redraws(client, db):
    r = client.get("/api/v1/crews/drawn")
    assert r.status_code == 200
    body = r.json()
    assert body["every"] >= 3600, "never shorter than the rebuild's own floor"
    assert body["drawn_s_ago"] is None or body["drawn_s_ago"] >= 0


def test_the_redraw_promise_follows_the_retention_cadence(client, db):
    """The rebuild rides the retention loop, so with retention set slower than an hour the
    panel used to promise a redraw the server had no intention of making."""
    ret = settings.get_retention(db)
    settings.set_retention(db, days=ret["days"], disk_floor_gb=ret["disk_floor_gb"],
                           interval_s=86400)
    assert client.get("/api/v1/crews/drawn").json()["every"] == 86400


# --- the lifecycle over the wire ---------------------------------------------------------

def test_rejoining_a_crew_over_http_is_not_a_500(client, db):
    """Reviewer-found, probed against the real endpoints: join, leave, join again used to be
    a permanent 500 that the client rendered as "That didn't work"."""
    _set_crews(db, cooldown_days=0)
    _rider(db, "w1")
    _signed_in(client, db, "w1")
    client.post("/api/v1/crews", json={"name": "In And Out", "join_policy": "open"})
    slug = db.query(Clan).filter(Clan.name == "In And Out").one().slug

    _rider(db, "w2")
    _signed_in(client, db, "w2")
    assert client.post(f"/api/v1/crews/{slug}/join", json={}).status_code == 200
    assert client.post("/api/v1/crews/leave", json={}).status_code == 200
    r = client.post(f"/api/v1/crews/{slug}/join", json={})
    assert r.status_code == 200, f"rejoining must work: {r.status_code} {r.text[:200]}"


def test_no_response_ever_shows_a_crews_retirement_tag(client, db):
    """`_retire` frees a folded crew's name by moving it out of the way, which means the tag
    must never reach a rider. It did, on the targets lookup and on /territory/at."""
    _rider(db, "lk1")
    _signed_in(client, db, "lk1")
    client.post("/api/v1/crews", json={"name": "Leaky", "join_policy": "open"})
    slug = db.query(Clan).filter(Clan.name == "Leaky").one().slug
    client.post(f"/api/v1/crews/{slug}/disband")

    for path in ("/api/v1/crews", "/api/v1/crews/me",
                 "/api/v1/territory/at?lat=59.9&lon=10.75"):
        r = client.get(path)
        assert "(folded" not in r.text, f"{path} leaks the retirement tag: {r.text[:200]}"


def test_the_emblem_route_is_shut_when_crews_are_off(client, db):
    """It was the one crew route that answered with the mode switched off, against an admin
    screen that says every one of them 404s."""
    _rider(db, "em1")
    _signed_in(client, db, "em1")
    client.post("/api/v1/crews", json={"name": "Badge", "join_policy": "open"})
    slug = db.query(Clan).filter(Clan.name == "Badge").one().slug
    assert client.get(f"/api/v1/crews/{slug}/emblem").status_code == 200
    _set_crews(db, enabled=False)
    assert client.get(f"/api/v1/crews/{slug}/emblem").status_code == 404


def test_a_rider_is_told_their_crew_folded(client, db):
    _rider(db, "fd1")
    _signed_in(client, db, "fd1")
    client.post("/api/v1/crews", json={"name": "Here Today", "join_policy": "open"})
    clan = db.query(Clan).filter(Clan.name == "Here Today").one()
    leader = client.cookies.get(pairing.COOKIE)

    _rider(db, "fd2")
    member = _signed_in(client, db, "fd2")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})

    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, leader)
    assert client.post(f"/api/v1/crews/{clan.slug}/disband").status_code == 200

    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, member)
    me = client.get("/api/v1/crews/me").json()
    assert me.get("folded") == "Here Today"
    assert client.get("/api/v1/crews/me").json().get("folded") is None
