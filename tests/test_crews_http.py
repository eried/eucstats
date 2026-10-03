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

from conftest import HANDLE


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
                    json={"store_id": HANDLE("d2"), "accept": False})
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
    client.post(f"/api/v1/crews/{clan.slug}/decide", json={"store_id": HANDLE("n2"), "accept": False})
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


def test_a_roster_never_publishes_a_riders_store_id(client, db):
    """A store_id is the only thing `POST /pair/confirm` needs, so printing one into a
    leader's panel hands them a session as that rider. A reviewer did exactly that and left
    the victim's crew for them. `Rider.public_id` exists for this and is what every other
    public surface uses."""
    _rider(db, "boss")
    # conftest pins public_id to store_id so board assertions read well, which would hide the
    # very thing this test is for. These two get real handles.
    for sid, h in (("boss", "pub-boss-9f2a"), ("victim", "pub-victim-4c81")):
        r = db.query(Rider).filter(Rider.store_id == sid).first()
        if r is not None:
            r.public_id = h
    db.commit()
    _signed_in(client, db, "boss")
    client.post("/api/v1/crews", json={"name": "The Firm", "join_policy": "open"})
    clan = db.query(Clan).filter(Clan.name == "The Firm").one()
    leader = client.cookies.get(pairing.COOKIE)

    _rider(db, "victim")
    v = db.query(Rider).filter(Rider.store_id == "victim").one()
    v.public_id = "pub-victim-4c81"
    db.commit()
    _signed_in(client, db, "victim")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})

    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, leader)
    me = client.get("/api/v1/crews/me").json()
    ids = [m["store_id"] for m in me["roster"]]
    assert ids, "the roster has to list somebody"
    assert "victim" not in ids and "boss" not in ids, (
        f"a real store_id reached the panel: {ids}")

    # and the published handle is useless for minting a session
    with pytest.raises(pairing.PairError):
        p = pairing.start(db, purpose="rider")
        pairing.confirm(db, p["code"], ids[0])


def test_the_published_handle_still_addresses_the_right_rider(client, db):
    _rider(db, "cap")
    _signed_in(client, db, "cap")
    client.post("/api/v1/crews", json={"name": "Handles", "join_policy": "open"})
    clan = db.query(Clan).filter(Clan.name == "Handles").one()
    leader = client.cookies.get(pairing.COOKIE)

    _rider(db, "crew")
    _signed_in(client, db, "crew")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})

    client.cookies.clear()
    client.cookies.set(pairing.COOKIE, leader)
    handle = db.query(Rider).filter(Rider.store_id == "crew").one().public_id
    r = client.post(f"/api/v1/crews/{clan.slug}/role",
                    json={"store_id": handle, "role": "officer"})
    assert r.status_code == 200, r.text
    assert crews.membership(db, "crew").role == "officer"


def test_no_endpoint_hands_the_browser_a_store_id(client, db):
    """`pair/confirm` treats a store_id as proof of identity, so any readable copy of one is a
    bearer token -- and unlike the session cookie it is permanent, cannot be made HttpOnly,
    and survives `revoke_all`, which is the documented answer to a lost phone. The pairing
    module's own docstring is the specification being guarded here: the browser "never sees
    it, never asks for it, and cannot make one up".

    Both of these printed it into their JSON body beside the cookie that was carefully kept
    out of reach, and a reviewer replayed one into a brand-new session.
    """
    _rider(db, "leaky")
    p = pairing.start(db, purpose="rider")
    pairing.confirm(db, p["code"], "leaky")
    # By value, not by substring: the test handle is "h-leaky", so a substring check would
    # fire on the very field that is meant to be published.
    def _values(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from _values(v)
        elif isinstance(o, list):
            for v in o:
                yield from _values(v)
        else:
            yield o

    poll = client.get("/api/v1/pair/poll", params={"token": p["token"]})
    assert poll.status_code == 200, poll.text
    assert "leaky" not in list(_values(poll.json())), f"the poll body leaks it: {poll.text}"

    me = client.get("/api/v1/crews/me")
    assert me.status_code == 200, me.text
    assert "leaky" not in list(_values(me.json())), f"/crews/me leaks it: {me.text}"
    assert me.json().get("handle") == HANDLE("leaky"), "the panel still needs its own name"

    # And the value it does publish is not a key to anything.
    p2 = pairing.start(db, purpose="rider")
    with pytest.raises(pairing.PairError):
        pairing.confirm(db, p2["code"], me.json()["handle"])


def test_an_officer_is_not_offered_buttons_aimed_at_themselves(client, db):
    """The roles list had a Remove and a Stand down on every non-leader row including the
    reader's own: Remove answered 400 on every press, and Stand down worked -- it stripped the
    clicker's own powers and took the roster panel with it. The panel can only tell its own
    row apart if the payload says who is reading it."""
    _rider(db, "of1")
    _signed_in(client, db, "of1")
    client.post("/api/v1/crews", json={"name": "Chain Of Command", "join_policy": "open"})
    clan = db.query(Clan).filter(Clan.name == "Chain Of Command").one()

    _rider(db, "of2")
    _signed_in(client, db, "of2")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})

    # An officer, because that is who gets the roster panel and therefore the buttons.
    _signed_in(client, db, "of1")
    r = client.post(f"/api/v1/crews/{clan.slug}/role",
                    json={"store_id": HANDLE("of2"), "role": "officer"})
    assert r.status_code == 200, r.text

    _signed_in(client, db, "of2")
    me = client.get("/api/v1/crews/me").json()
    assert me["handle"] == HANDLE("of2")
    assert me["role"] == "officer"
    mine = [x for x in me["roster"] if x["store_id"] == me["handle"]]
    assert len(mine) == 1, "the reader has to be findable in their own roster"


def test_an_admin_pass_is_refused_at_the_rider_door(client, db):
    """The strip that keeps `store_id` out of the poll body sat inside the branch that mints a
    rider session. An admin pairing returns no session, so it fell straight past -- and the
    public, unauthenticated rider route answered with the raw store_id and consumed the
    pairing doing it."""
    _rider(db, "chief2")
    p = pairing.start(db, purpose="admin")
    pairing.confirm(db, p["code"], "chief2")
    r = client.get("/api/v1/pair/poll", params={"token": p["token"]})
    assert r.status_code == 410, r.text
    assert "chief2" not in r.text


def test_a_handle_that_echoes_the_store_id_is_replaced(client, db):
    """A handle has one job: to be publishable where the store_id is not. A row whose handle
    contains the store_id fails that job while looking fine, and a reviewer found such a row
    and rode it to a working session -- read the handle, strip the prefix, confirm a pairing.
    Both minters make random hex, so a row like that can only come from something else."""
    from web.crews_api import _handle
    _rider(db, "echo")
    r = db.query(Rider).filter(Rider.store_id == "echo").one()
    r.public_id = "h-echo"          # the shape the reviewer found in the wild
    db.commit()

    h = _handle(db, "echo")
    assert "echo" not in h, f"the handle still gives the store_id away: {h}"
    db.refresh(r)
    assert r.public_id == h, "and the row is fixed, not just the answer"


def test_the_panel_routes_refuse_to_be_cached(client, db):
    """Both answers depend on a cookie. `/crews/me` carries the rider's handle, their crew's
    invite code, the roster, who is waiting and who was turned away; `/crews/{slug}` carries
    `targets`, which this module calls a crew's plan for its own week. `/territory` beside
    them is `public, max-age=60` because it is the same for everybody. A shared cache in front
    of these two would hand one rider another crew's panel."""
    _rider(db, "cc1")
    _signed_in(client, db, "cc1")
    r = client.post("/api/v1/crews", json={"name": "No Cache", "join_policy": "open"})
    assert r.status_code == 200, r.text
    clan = db.query(Clan).filter(Clan.name == "No Cache").one()

    for url in ("/api/v1/crews/me", f"/api/v1/crews/{clan.slug}"):
        res = client.get(url)
        assert res.status_code == 200, res.text
        cc = res.headers.get("cache-control", "")
        assert "no-store" in cc and "private" in cc, f"{url} -> {cc!r}"
        assert "Cookie" in res.headers.get("vary", ""), f"{url} vary: {res.headers.get('vary')!r}"


def test_no_roster_row_hands_out_a_guessable_handle(client, db):
    """`_handle` guards the reader's own handle; `_hd` publishes everybody else's -- the whole
    roster, every pending request and every refusal -- and skipped the containment check. A
    reviewer read a polluted handle out of a leader's own roster, stripped the prefix and
    minted a working session as that rider. The guard had been added to the one place that
    needed it least."""
    _rider(db, "chief3")
    _signed_in(client, db, "chief3")
    r = client.post("/api/v1/crews", json={"name": "Open Doors", "join_policy": "open"})
    assert r.status_code == 200, r.text
    clan = db.query(Clan).filter(Clan.name == "Open Doors").one()

    _rider(db, "mate")
    _signed_in(client, db, "mate")
    client.post(f"/api/v1/crews/{clan.slug}/join", json={})

    # a row written by something that did not go through either minter
    row = db.query(Rider).filter(Rider.store_id == "mate").one()
    row.public_id = "h-mate"
    db.commit()

    _signed_in(client, db, "chief3")
    me = client.get("/api/v1/crews/me").json()
    published = [x["store_id"] for x in me.get("roster", [])]
    assert published, "the leader should see a roster"
    for h in published:
        assert "mate" not in h and "chief3" not in h, f"roster hands out {h}"
    db.refresh(row)
    assert "mate" not in (row.public_id or ""), "and the row is repaired, not just the answer"


def test_an_admin_pass_is_refused_without_being_spent(client, db):
    """Refusing the admin pass after `poll` had already deleted the token closed the leak but
    still burned the pairing: an admin who polled the wrong screen once had to start over, and
    the second attempt said "unknown" instead of saying why."""
    _rider(db, "chief4")
    p = pairing.start(db, purpose="admin")
    pairing.confirm(db, p["code"], "chief4")

    first = client.get("/api/v1/pair/poll", params={"token": p["token"]})
    assert first.status_code == 410, first.text
    assert "chief4" not in first.text
    assert "wrong_screen" in first.text

    again = client.get("/api/v1/pair/poll", params={"token": p["token"]})
    assert again.status_code == 410
    assert "wrong_screen" in again.text, "the pairing survives, and still says why"
