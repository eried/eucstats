"""Every crews scenario, end to end, against a local server.

This is the answer to "how do you test all of it without a room full of phones". Each scenario
below is a thing a rider or a leader actually does, driven through the real HTTP endpoints with
real cookies and real pairing handshakes. The script plays the part of the phone at the one
moment the phone matters — `POST /api/v1/pair/confirm`, which is the whole of the app's
involvement in crews — and otherwise behaves exactly like a browser.

What that means in practice: the app does not need to exist for any of this to be tested, and
when the app does arrive, the only thing left to check on the device is that the camera decodes
a QR code and that one POST leaves the handset. That is a ten-minute check, not a test plan.

    python scripts/sim_scenarios.py --base http://127.0.0.1:8000

Every scenario is independent and idempotent-ish: it uses its own riders, named after itself,
so a failure in one does not cascade and a rerun does not need a wiped database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import uuid
from datetime import datetime, timedelta

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from sim_fleet import ride_csv, register, upload  # noqa: E402

PASS, FAIL, SKIP = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m", "\033[33mSKIP\033[0m"
results: list[tuple[str, bool, str]] = []
VICTIM_SLUG = [""]          # the founder crew, which the security scenario tries to hijack


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL}  {name}{'  — ' + detail if detail else ''}")
    return ok


class Rider:
    """A simulated rider: an app that uploads, and a browser that pairs."""

    def __init__(self, base: str, store_id: str, name: str, flag: str = "NO",
                 lat: float = 0.0, lon: float = 0.0):
        self.base, self.store_id = base, store_id
        # Display names are unique site-wide, so every rider carries the run's tag. Without it
        # a second run of the suite fails at the first registration with "name already taken",
        # which is the server being right and the test being careless.
        suffix = store_id.split("-")[0][-4:]
        self.name = f"{name[:15].strip()} {suffix}"[:20]      # the site caps names at 20
        # Each run of the suite rides its own patch of ground, derived from the run tag. With
        # every rider launching from one hard-coded coordinate, a second run found the first
        # run's crews already holding those tiles, the wins split, no crew formed a 2x2 block
        # and the whole territory scenario reported nothing. The seed rule was right; the test
        # was making every crew in history fight over one street corner.
        h = int(hashlib.sha1(store_id.split("-")[0].encode()).hexdigest()[:8], 16)
        self.lat = lat or (55.0 + (h % 900) / 100.0)          # 55.00-64.00 N
        self.lon = lon or (6.0 + (h // 900 % 1400) / 100.0)   # 6.00-20.00 E
        self.web = requests.Session()            # the browser: holds the crew_session cookie
        register(base, store_id, self.name, flag)

    # --- the app's side
    def ride(self, km: float = 12.0, days_ago: int = 1, cruise: float = 28.0) -> dict:
        rng = random.Random(hash((self.store_id, days_ago, km)) & 0xFFFF)
        start = (datetime.utcnow() - timedelta(days=days_ago)).replace(
            hour=9, minute=rng.randrange(60), second=0, microsecond=0)
        csv_text, info = ride_csv(rng, self.lat, self.lon, start, km, cruise)
        return upload(self.base, self.store_id, csv_text, start, info["end"],
                      ("Begode", "Master", "GW2026202"), 60)

    def approve(self, code: str) -> dict:
        """What the phone does after the rider taps Approve on the pairing screen."""
        r = requests.post(f"{self.base}/api/v1/pair/confirm",
                          json={"code": code, "store_id": self.store_id}, timeout=20)
        return {"status": r.status_code, **(r.json() if r.content else {})}

    # --- the browser's side
    def pair(self) -> bool:
        s = self.web.post(f"{self.base}/api/v1/pair/start", timeout=20)
        if s.status_code != 200:
            return False
        p = s.json()
        # the app sees the QR and confirms; the browser is still holding the token
        self.approve(p["code"])
        r = self.web.get(f"{self.base}/api/v1/pair/poll", params={"token": p["token"]},
                         timeout=20)
        return r.status_code == 200 and r.json().get("status") == "paired"

    def api(self, method: str, path: str, **kw) -> tuple[int, dict]:
        r = self.web.request(method, f"{self.base}{path}", timeout=30, **kw)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:200]}
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            try:                                 # CrewError travels as JSON inside `detail`
                body["detail"] = json.loads(body["detail"])
            except Exception:
                pass
        return r.status_code, body

    def me(self) -> dict:
        return self.api("GET", "/api/v1/crews/me")[1]


def err_code(body: dict) -> str:
    d = body.get("detail")
    if isinstance(d, dict):
        return d.get("code", "")
    return str(d or "")


# --- the scenarios ------------------------------------------------------------------------

def scenario_ride_without_a_crew(base: str, tag: str) -> None:
    """1) Riding normally. Nothing about crews should touch a rider who is not in one."""
    print("\n1. Riding normally, no crew")
    r = Rider(base, f"{tag}-solo", "SIM·Solo Rider")
    res = r.ride(km=9.0, days_ago=2)
    check("a ride uploads and validates", res.get("validation_status") == "validated",
          str(res.get("reasons") or ""))
    from database import SessionLocal
    from models import Trip
    db = SessionLocal()
    t = db.query(Trip).filter(Trip.rider_store_id == r.store_id).first()
    check("the ride carries no crew", t is not None and t.clan_id is None)
    db.close()


def scenario_found_a_crew(base: str, tag: str) -> Rider:
    """2) Riding, then founding a crew with everything that comes with one."""
    print("\n2. Founding a crew")
    r = Rider(base, f"{tag}-founder", "SIM·Founder")
    r.ride(km=14.0, days_ago=3)

    check("a browser pairs by QR with no password", r.pair())
    me = r.me()
    # `handle`, not `store_id`: the panel is told an opaque handle because a store_id is
    # proof of identity at pair/confirm. This script asserted the old field and went red on
    # the feature it exists to demonstrate.
    check("the paired session knows who it is", bool(me.get("handle")))
    check("one validated ride is enough to found", me.get("can_found") is True)

    code, ident = r.api("GET", "/api/v1/crews/identity")
    check("a colour and pattern are suggested, not asked for",
          code == 200 and ident.get("colour", "").startswith("#"),
          f"{ident.get('colour')} {ident.get('pattern')}")

    code, body = r.api("POST", "/api/v1/crews", json={
        "name": f"Nordlys Collective {tag[-4:]}", "description": "Oslo, mostly after dark.",
        "colour": ident["colour"], "pattern": ident["pattern"], "join_policy": "approval"})
    if not check("the crew is created", code == 200, err_code(body)):
        return r
    r.crew_slug = body["crew"]["slug"]
    VICTIM_SLUG[0] = r.crew_slug
    check("the founder leads it", r.me().get("role") == "leader")
    check("their recent rides were adopted by the crew", _trips_in_crew(r.store_id) > 0,
          f"{_trips_in_crew(r.store_id)} trips")

    bad = r.api("POST", "/api/v1/crews", json={"name": f"Second Crew {tag[-4:]}"})
    check("they cannot found a second crew", err_code(bad[1]) == "already_in_crew")

    short = r.api("POST", f"/api/v1/crews/{r.crew_slug}/edit", json={"name": "x"})
    check("a one-character name is refused", err_code(short[1]) == "bad_name")
    return r


def _slug_to_id(db, slug):
    from models import Clan
    c = db.query(Clan).filter(Clan.slug == slug).first()
    return c.clan_id if c else None


def _trips_in_crew(store_id: str) -> int:
    from database import SessionLocal
    from models import Trip
    db = SessionLocal()
    n = db.query(Trip).filter(Trip.rider_store_id == store_id,
                              Trip.clan_id.isnot(None)).count()
    db.close()
    return n


def scenario_territory(base: str, tag: str, leader: Rider) -> None:
    """3) Riding and collecting ground, and watching it appear on the map."""
    print("\n3. Collecting territory")
    from database import SessionLocal
    from services import settings, territory
    db = SessionLocal()
    cfg = settings.get_crews(db)

    # one tile is about 2.4 km across at Oslo, so a 2x2 seed needs riding over roughly 5 km of
    # ground in both directions — which is what these loops do
    for i, km in enumerate((26.0, 30.0, 22.0, 28.0)):
        leader.ride(km=km, days_ago=5 + i, cruise=30.0)
    rep = territory.rebuild(db, window_days=cfg["window_days"], zoom=cfg["zoom"],
                            seed=cfg["seed"])
    check("the rebuild produces territory", rep["held"] > 0, str(rep))
    check("every held tile is in a region", rep["regions"] > 0)

    rank = territory.ranking(db, limit=10)
    mine = [x for x in rank if x["slug"] == leader.crew_slug]
    check("the crew is on the ranking with an area in km2",
          bool(mine) and mine[0]["km2"] > 0,
          f"{mine[0]['km2']} km2 over {mine[0]['tiles']} tiles" if mine else "absent")

    code, body = leader.api("GET", "/api/v1/territory")
    check("the map payload is served from cache", code == 200 and body.get("cells") is not None,
          f"{len(body.get('cells', [])) // 3} cells, {len(body.get('crews', []))} crews")

    # Probe a tile the crew actually holds, not the middle of the loop. The rides are rings
    # around a home point, and once tiles are small enough a ring does not cover its own
    # centre, so asking about the centre asks about ground nobody rode.
    from models import ClanCell
    from services import tiles as T
    held = (db.query(ClanCell)
            .filter(ClanCell.clan_id == _slug_to_id(db, leader.crew_slug)).first())
    if held is None:
        check("a point on the map says who holds it", False, "the crew holds no tiles")
    else:
        w, s_, e, n = T.bounds(held.tile)
        code, at = leader.api("GET", "/api/v1/territory/at",
                              params={"lat": (s_ + n) / 2, "lon": (w + e) / 2})
        check("a point on the map says who holds it",
              code == 200 and (at.get("crew") or {}).get("slug") == leader.crew_slug,
              (at.get("crew") or {}).get("name") or "nobody")

    # "where to ride next" — the only thing in the mode that answers the question a rider
    # actually has. It is their own crew's list and nobody else's: on a rival's page the same
    # rows would read as a list of weak spots.
    code, body = leader.api("GET", f"/api/v1/crews/{leader.crew_slug}")
    tg = body.get("targets") or []
    check("the crew is told where to ride next", code == 200 and bool(tg),
          f"{len(tg)} squares, nearest {tg[0]['need']} km" if tg else "no list")
    check("the list never points at ground the crew already holds",
          all(not (t.get("held_by") == _slug_to_id(db, leader.crew_slug)) for t in tg))
    out = requests.get(f"{base}/api/v1/crews/{leader.crew_slug}", timeout=20)   # no cookie
    leaked = out.json().get("targets") or []
    check("a stranger is not handed the crew's target list",
          out.status_code == 200 and not leaked, f"{len(leaked)} squares leaked")
    db.close()


def scenario_joining(base: str, tag: str) -> None:
    """5) The three join policies, each behaving differently."""
    print("\n5. Joining: open, approval, invite")
    hosts = {}
    for policy in ("open", "approval", "invite"):
        h = Rider(base, f"{tag}-host-{policy}", f"SIM·Host {policy.title()}")
        h.ride(km=11.0, days_ago=4)
        h.pair()
        code, ident = h.api("GET", "/api/v1/crews/identity")
        c, body = h.api("POST", "/api/v1/crews", json={
            "name": f"Test {policy.title()} {tag[-4:]}", "colour": ident["colour"],
            "pattern": ident["pattern"], "join_policy": policy})
        if not check(f"a {policy} crew exists", c == 200, err_code(body)):
            continue
        hosts[policy] = (h, body["crew"]["slug"])

    if "open" in hosts:
        j = Rider(base, f"{tag}-join-open", "SIM·Joins Open")
        j.ride(km=6.0, days_ago=3)
        j.pair()
        c, b = j.api("POST", f"/api/v1/crews/{hosts['open'][1]}/join", json={})
        check("open: in straight away", c == 200 and b.get("status") == "active", err_code(b))

    if "approval" in hosts:
        host, slug = hosts["approval"]
        j = Rider(base, f"{tag}-join-appr", "SIM·Awaits Approval")
        j.ride(km=6.0, days_ago=3)
        j.pair()
        c, b = j.api("POST", f"/api/v1/crews/{slug}/join", json={})
        check("approval: lands as pending", c == 200 and b.get("status") == "pending",
              err_code(b))
        pend = host.me().get("pending") or []
        # Rows carry handles, and `decide` takes the handle the panel was given. Sending a
        # raw store_id here answered `no_request`, which read as the approval flow being
        # broken when it was this script holding the wrong end.
        joiner = (j.me() or {}).get("handle")
        check("the leader sees the request", any(p["store_id"] == joiner for p in pend))
        c, b = host.api("POST", f"/api/v1/crews/{slug}/decide",
                        json={"store_id": joiner, "accept": True})
        check("the leader can accept it", c == 200, err_code(b))
        check("the new member is active", j.me().get("status") == "active")
        check("their rides joined the crew too", _trips_in_crew(j.store_id) > 0)

    if "invite" in hosts:
        host, slug = hosts["invite"]
        j = Rider(base, f"{tag}-join-inv", "SIM·Knows The Code")
        j.ride(km=6.0, days_ago=3)
        j.pair()
        c, b = j.api("POST", f"/api/v1/crews/{slug}/join", json={})
        check("invite: refused without a code", err_code(b) == "bad_invite", err_code(b))
        c, b = j.api("POST", f"/api/v1/crews/{slug}/join", json={"invite_code": "WRONGCOD"})
        check("invite: refused with the wrong code", err_code(b) == "bad_invite")
        real = (host.me().get("crew") or {}).get("invite_code")
        c, b = j.api("POST", f"/api/v1/crews/{slug}/join", json={"invite_code": real})
        check("invite: accepted with the right code", c == 200 and b.get("status") == "active",
              err_code(b))


def scenario_switching_and_renaming(base: str, tag: str) -> None:
    """4) Switching crews, renaming your own, leaving, and the cooldown that bites."""
    print("\n4. Switching, renaming, leaving")
    a = Rider(base, f"{tag}-sw-a", "SIM·Switcher")
    a.ride(km=8.0, days_ago=4)
    a.pair()
    code, ident = a.api("GET", "/api/v1/crews/identity")
    c, body = a.api("POST", "/api/v1/crews", json={
        "name": f"First Home {tag[-4:]}", "colour": ident["colour"], "pattern": ident["pattern"],
        "join_policy": "open"})
    if not check("a crew to leave exists", c == 200, err_code(body)):
        return
    slug = body["crew"]["slug"]

    c, b = a.api("POST", f"/api/v1/crews/{slug}/edit",
                 json={"name": f"First Home Renamed {tag[-4:]}", "description": "Now with a new name."})
    check("a leader can rename their own crew", c == 200, err_code(b))
    check("the rename stuck", (a.me().get("crew") or {}).get("name") == f"First Home Renamed {tag[-4:]}")
    check("the link did not rot", (a.me().get("crew") or {}).get("slug") == slug,
          "slug kept so old links still work")

    # a lone leader may leave: there is nobody to abandon
    c, b = a.api("POST", "/api/v1/crews/leave", json={})
    check("a lone leader can leave", c == 200, err_code(b))
    check("the rides keep the crew that earned them", _trips_in_crew(a.store_id) > 0,
          "territory is a record, not a live query")

    c, b = a.api("POST", "/api/v1/crews", json={"name": f"Immediately Another {tag[-4:]}"})
    check("the 7-day cooldown blocks an instant switch", err_code(b) == "cooldown",
          err_code(b))

    # a leader with members must hand over first
    host = Rider(base, f"{tag}-sw-host", "SIM·Has Members")
    host.ride(km=8.0, days_ago=4)
    host.pair()
    code, ident = host.api("GET", "/api/v1/crews/identity")
    c, body = host.api("POST", "/api/v1/crews", json={
        "name": f"Crowded Crew {tag[-4:]}", "colour": ident["colour"], "pattern": ident["pattern"],
        "join_policy": "open"})
    if c != 200:
        return
    hslug = body["crew"]["slug"]
    mem = Rider(base, f"{tag}-sw-mem", "SIM·Follower")
    mem.ride(km=5.0, days_ago=3)
    mem.pair()
    mem.api("POST", f"/api/v1/crews/{hslug}/join", json={})
    c, b = host.api("POST", "/api/v1/crews/leave", json={})
    check("a leader with members must promote someone first",
          err_code(b) == "promote_first", err_code(b))
    # the handle again: /role takes what the roster published, not the store_id
    c, b = host.api("POST", f"/api/v1/crews/{hslug}/role",
                    json={"store_id": (mem.me() or {}).get("handle"), "role": "officer"})
    check("promoting an officer works", c == 200, err_code(b))
    c, b = host.api("POST", "/api/v1/crews/leave", json={})
    check("and then the leader may go", c == 200, err_code(b))


def scenario_idle_leader(base: str, tag: str) -> None:
    """4b) A leader who stops riding, and the member who takes over without an admin."""
    print("\n4b. The leader who went quiet")
    from database import SessionLocal
    from models import ClanMember, utcnow
    lead = Rider(base, f"{tag}-idle-lead", "SIM·Gone Quiet")
    lead.ride(km=9.0, days_ago=4)
    lead.pair()
    code, ident = lead.api("GET", "/api/v1/crews/identity")
    c, body = lead.api("POST", "/api/v1/crews", json={
        "name": f"Dormant Crew {tag[-4:]}", "colour": ident["colour"], "pattern": ident["pattern"],
        "join_policy": "open"})
    if not check("a crew with a leader exists", c == 200, err_code(body)):
        return
    slug = body["crew"]["slug"]

    mem = Rider(base, f"{tag}-idle-mem", "SIM·Still Here")
    mem.ride(km=7.0, days_ago=3)
    mem.pair()
    mem.api("POST", f"/api/v1/crews/{slug}/join", json={})

    c, b = mem.api("POST", f"/api/v1/crews/{slug}/claim", json={})
    check("a member cannot claim an active leader's crew",
          err_code(b) == "leader_active", err_code(b))

    db = SessionLocal()                          # wind the leader's clock back 100 days
    m = (db.query(ClanMember)
         .filter(ClanMember.store_id == lead.store_id, ClanMember.role == "leader").first())
    m.last_seen = utcnow() - timedelta(days=100)
    db.commit()
    db.close()

    c, b = mem.api("POST", f"/api/v1/crews/{slug}/claim", json={})
    check("after 90 days idle the longest-serving member takes over", c == 200, err_code(b))
    check("they are now the leader", mem.me().get("role") == "leader")


def scenario_security(base: str, tag: str) -> None:
    """The things that must NOT work."""
    print("\n6. What must not work")
    anon = requests.Session()
    r = anon.post(f"{base}/api/v1/crews", json={"name": f"No Session Crew {tag[-4:]}"}, timeout=20)
    check("an unpaired browser cannot create a crew", r.status_code == 401,
          str(r.status_code))

    r = anon.get(f"{base}/api/v1/pair/poll", params={"token": "made-up-token"}, timeout=20)
    check("a made-up pairing token is rejected", r.status_code == 410, str(r.status_code))

    r = anon.post(f"{base}/api/v1/pair/confirm",
                  json={"code": "ZZZZZZ", "store_id": f"{tag}-solo"}, timeout=20)
    check("a code that was never issued cannot be confirmed", r.status_code == 410,
          str(r.status_code))

    # a paired session is crew-scoped: it must not be able to act as the app
    victim = Rider(base, f"{tag}-scope", "SIM·Scope Check")
    victim.ride(km=5.0, days_ago=2)
    victim.pair()
    r = victim.web.post(f"{base}/api/v1/riders",
                        json={"store_id": victim.store_id, "display_name": "Renamed By Web"},
                        timeout=20)
    # the rider endpoint does not read the crew cookie at all, which is the point: a crew
    # session is not an upload identity and cannot become one
    check("a crew session is not an upload identity",
          "crew_session" not in str(r.request.body or ""),
          "the cookie carries no weight outside crew endpoints")

    outsider = Rider(base, f"{tag}-outsider", "SIM·Outsider")
    outsider.ride(km=5.0, days_ago=2)
    outsider.pair()
    c, b = outsider.api("POST", f"/api/v1/crews/{VICTIM_SLUG[0]}/edit",
                        json={"name": "Hijacked"})
    check("a non-member cannot edit somebody else's crew", c == 403, str(c))
    c, b = outsider.api("POST", f"/api/v1/crews/{VICTIM_SLUG[0]}/disband", json={})
    check("a non-member cannot disband it either", c in (400, 403), str(c))


def scenario_kill_switch(base: str, tag: str) -> None:
    """8) The whole mode switches off without losing a row."""
    print("\n7. The kill switch")
    from database import SessionLocal
    from models import Clan
    from services import settings
    db = SessionLocal()
    before = db.query(Clan).count()
    cfg = settings.get_crews(db)
    settings.set_crews(db, False, cfg["zoom"], cfg["window_days"], cfg["seed"],
                       cfg["cooldown_days"], cfg["max_members"], cfg["opacity"],
                       cfg["creation_open"])
    db.close()
    r = requests.get(f"{base}/api/v1/crews", timeout=20)
    check("with crews off, the endpoints are simply not there", r.status_code == 404,
          str(r.status_code))
    r = requests.get(f"{base}/api/v1/territory", timeout=20)
    check("the territory payload is gone too", r.status_code == 404, str(r.status_code))

    db = SessionLocal()
    settings.set_crews(db, True, cfg["zoom"], cfg["window_days"], cfg["seed"],
                       cfg["cooldown_days"], cfg["max_members"], cfg["opacity"],
                       cfg["creation_open"])
    after = db.query(Clan).count()
    db.close()
    check("switching it back on loses nothing", after == before, f"{after} crews")
    r = requests.get(f"{base}/api/v1/crews", timeout=20)
    check("and the crews are all still there", r.status_code == 200, str(r.status_code))


def _sweep_run(db, tag: str) -> None:
    """Take the run's crews back off the board.

    The rate limits above are raised for the run and restored at the end, with a comment
    saying why that discipline matters. The crews were not: `scenario_found_a_crew` founds
    "Nordlys Collective <tag>" and nothing ever ends it, so every run left a permanent crew on
    the demo board named after four characters of a rider id, carrying the real Nordlys
    Collective's own description, and holding whatever ground its simulated rider had ridden.

    Three had accumulated by the time anybody noticed, sitting at ranks 3, 4 and 6 -- a board
    that reads as a test harness to anybody being shown the product. Twelve review passes saw
    it and none could fix it, because the mess is made by the tool that checks the thing
    rather than by the thing.

    Matched on this run's own tag, so it can only ever remove what this run made.
    """
    from models import Clan, ClanMember
    frag = tag[-4:]
    gone = 0
    for c in db.query(Clan).all():
        if frag not in (c.name or ""):
            continue
        db.query(ClanMember).filter(ClanMember.clan_id == c.clan_id).delete(
            synchronize_session=False)
        db.delete(c)
        gone += 1
    if gone:
        db.commit()
        print(f"swept {gone} crew(s) this run created")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--tag", default="", help="prefix for this run's riders (default: random)")
    ap.add_argument("--only", default="", help="run one scenario by number, e.g. 3")
    a = ap.parse_args()
    tag = a.tag or f"sc{uuid.uuid4().hex[:6]}"

    from database import SessionLocal
    from services import settings
    db = SessionLocal()
    # The suite registers a dozen-odd riders from one address, which is exactly the shape the
    # per-IP creation limit exists to stop. Raised for the run and restored at the end, rather
    # than disabled in the server: the limit is a real defence and should stay testable.
    rl_before = settings.get_rate_limits(db)
    settings.set_rate_limits(db, {**rl_before, "rider_create_per_ip": 500,
                                  "trip_per_rider": 2000, "trip_per_ip": 5000,
                                  "pair_start_per_ip": 500, "pair_confirm_per_ip": 500,
                                  "pair_confirm_per_rider": 50,
                                  "crew_write_per_session": 500})
    cfg = settings.get_crews(db)
    if not cfg["enabled"]:
        settings.set_crews(db, True, cfg["zoom"], cfg["window_days"], cfg["seed"],
                           cfg["cooldown_days"], cfg["max_members"], cfg["opacity"], True)
        print("crews were switched off; switched on for this run")
    db.close()

    print(f"Scenarios against {a.base}  (riders tagged {tag})")
    scenario_ride_without_a_crew(a.base, tag)
    leader = scenario_found_a_crew(a.base, tag)
    scenario_territory(a.base, tag, leader)
    scenario_switching_and_renaming(a.base, tag)
    scenario_idle_leader(a.base, tag)
    scenario_joining(a.base, tag)
    scenario_security(a.base, tag)
    scenario_kill_switch(a.base, tag)

    db = SessionLocal()
    settings.set_rate_limits(db, rl_before)
    # And the crews, for the same reason the limits are put back: a run should leave the
    # board the way it found it. See `_sweep_run`.
    _sweep_run(db, tag)
    db.close()

    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    for name, _ok, detail in bad:
        print(f"  FAILED: {name}  {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
