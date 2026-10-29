"""The guard the other 842 could not be: a browser that opens the panel.

Every defect this feature shipped in round 20 was found by a reviewer driving a browser, and
not one by the test suite. Four of them were the same shape -- code that renders and does
nothing:

* `primeDock` asked for the pending count once on load and never again, so a leader was never
  told somebody was knocking.
* `placeTip` read `offsetWidth` off a box that shrink-to-fit had already collapsed, so the map
  tip became a 36px-wide, 626px-tall ribbon at the right edge.
* The waiting state rendered a filter and a "Show all 22" and never bound either, so both were
  inert and every row rendered unfolded, making the button's own label false.
* A count named `cold` was shadowed by an array of the same name further down the function and
  printed "[object Object]" seven times.

A fifth was worse than inert: a five-row cap meant seven of forty-four at-risk squares were
never rendered at all -- no place, no distance, not clickable.

None of those is visible to a test that reads source text or speaks HTTP. All of them are
obvious to anything that opens the page. So this file opens the page.

It needs the `playwright` package and its browser, and SKIPS cleanly without them -- the same
arrangement the node-based guards here already use for `node`. Nothing is added to
requirements.txt, so the repo takes on no dependency it has not asked for; anyone who wants
these to run installs it:

    pip install playwright && playwright install chromium

Each test below is named for the bug it would have caught.
"""
import os
import socket
import threading
import time
from datetime import timedelta

import pytest

pytest.importorskip("playwright", reason="needs the playwright package to drive a browser")
from playwright.sync_api import sync_playwright  # noqa: E402

import models  # noqa: E402
from services import crews, settings, territory  # noqa: E402

import pathlib  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))
from i18n import EN  # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def site():
    """The real app on a real port, in a thread, against the isolated test data dir.

    `conftest` already points `EUCSTATS_DATA_DIR` at a temp directory before anything imports
    config, so this serves the same throwaway world the rest of the suite uses.
    """
    import uvicorn

    from main import app

    port = _free_port()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):                       # the thread needs a moment to bind
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        pytest.skip("the app did not start in time")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def _serpentine(tile_x, tile_y, zoom, rows=3, cols=3, per_leg=36):
    """A ride that actually covers a block of squares, as lat/lon pairs.

    Territory is earned per square from the track, so a crew with no track holds nothing and
    `buildEmblems` never runs. Walking the centre line of each tile row end to end puts about
    a tile's width of riding into every square it crosses, which clears `min_lead_km` (0.42 of
    an edge) with room to spare at any latitude.
    """
    from services import tiles as T

    pts = []
    for j in range(rows):
        west, south, east, north = T.bounds(f"{zoom}/{tile_x}/{tile_y + j}")
        lat = (south + north) / 2.0
        span = T.bounds(f"{zoom}/{tile_x + cols - 1}/{tile_y + j}")[2] - west
        lons = [west + (span * (i + 0.5) / (cols * per_leg)) for i in range(cols * per_leg)]
        if j % 2:                               # serpentine, so the legs join up
            lons.reverse()
        pts += [(lat, lon) for lon in lons]
    return pts


def _give_ground(db, store_id, clan_id, lat=59.9139, lon=10.7522, zoom=14,
                 rows=3, cols=3, tag=""):
    """Ride a block of squares for one crew, so the map has an emblem to paint.

    This is what the file used to lack. Three tests here had to build a bare element and ask
    the stylesheets a question, because no crew in this world held any ground and no `.crewemb`
    was ever created -- which left the one thing worth checking, whether a badge is painted
    where the map projected it, outside the suite's reach.

    `tag` is what lets one crew hold ground in two places: the trip's uuid is derived from the
    crew, so a second call without one is a no-op on an existing row. A crew with exactly one
    patch cannot answer the question "does Highlight light everything it holds", because
    everything it holds IS the patch.
    """
    from datetime import timedelta

    from models import Trip, TripTrack, utcnow
    from services import tiles as T
    from services import territory

    home = T.parse(T.tile_of(lat, lon, zoom))
    pts = _serpentine(home[1], home[2], zoom, rows=rows, cols=cols)
    when = utcnow() - timedelta(days=2)
    uuid = f"brw-ground-{clan_id}{tag}"
    if db.get(Trip, uuid) is None:
        db.add(Trip(trip_uuid=uuid, rider_store_id=store_id, clan_id=clan_id,
                    distance_km=12.0, validation_status="validated",
                    start_utc=when, end_utc=when + timedelta(hours=1),
                    start_lat=pts[0][0], start_lon=pts[0][1]))
        db.add(TripTrack(trip_uuid=uuid, points=_track(when, pts)))
        db.commit()
    return territory.rebuild(db, window_days=365, zoom=zoom, seed=2)


def _track(start, pts):
    """A track blob in the shape the ingest pipeline writes. Same helper as
    `tests/test_territory.py`: raw zlib pairs decode to nothing and silently earn no ground."""
    from datetime import timedelta

    from ingest.downsample import encode_track

    class S:
        __slots__ = ("t", "lat", "lon", "speed", "g")

        def __init__(self, t, lat, lon):
            self.t, self.lat, self.lon, self.speed, self.g = t, lat, lon, 20.0, 0.0

    return encode_track([S(start + timedelta(seconds=i * 5), a, b)
                         for i, (a, b) in enumerate(pts)])


@pytest.fixture(scope="module")
def world(site):
    """One leader, one rival closing in, and enough squares to exercise the cards."""
    from database import SessionLocal

    db = SessionLocal()
    try:
        settings.set_crews(db, enabled=True, zoom=14, window_days=365, seed=2, cooldown_days=7,
                           max_members=0, opacity=0.55, creation_open=True)
        for sid in ("brw-lead", "brw-rival"):
            if db.get(models.Rider, sid) is None:
                db.add(models.Rider(store_id=sid, display_name=sid,
                                    platform="google_play", flag="NO"))
                db.commit()
                db.add(models.Trip(trip_uuid=f"brw-seed-{sid}", rider_store_id=sid,
                                   distance_km=5.0, validation_status="validated",
                                   start_utc=models.utcnow() - timedelta(days=2),
                                   end_utc=models.utcnow() - timedelta(days=2)))
                db.commit()
        mine = crews.membership(db, "brw-lead")
        if mine is None:
            crews.create(db, "brw-lead", "Browser Crew", "", join_policy="approval")
        theirs = crews.membership(db, "brw-rival")
        if theirs is None:
            crews.create(db, "brw-rival", "Rival Crew", "", join_policy="open")
        db.commit()
        # And ground under one of them. Without this the map paints no emblem at all, which is
        # what kept the badge tests here down to questions about the stylesheet.
        mine = crews.membership(db, "brw-lead")
        report = _give_ground(db, "brw-lead", mine.clan_id)
        yield {"base": site, "territory": report, "clan_id": mine.clan_id}
    finally:
        db.close()


@pytest.fixture(scope="module")
def browser():
    """One browser for the whole module.

    The sync API allows exactly one live `sync_playwright()` per thread, so the second fixture
    that opened its own died with "Please use the Async API instead" and took every test using
    the first one down with it. Contexts are the cheap part; the driver is not.
    """
    with sync_playwright() as pw:
        try:
            br = pw.chromium.launch()
        except Exception as exc:                # no browser binary installed
            pytest.skip(f"playwright has no browser available: {exc}")
        yield br
        br.close()


def _page(browser, world, **ctx_args):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, **ctx_args)
    pg = ctx.new_page()
    pg.goto(world["base"], wait_until="domcontentloaded")
    return ctx, pg


@pytest.fixture(scope="module")
def page(browser, world):
    ctx, pg = _page(browser, world)
    yield pg
    ctx.close()


def _open_panel(page):
    """Wait for the map to finish loading, then press the dock button.

    The dock is `inert` until the map's load event, which is why a scripted click placed any
    earlier is swallowed -- itself a thing that cost a reviewer an hour.
    """
    page.wait_for_function(
        "() => { const i = document.querySelector('.intro');"
        "        return i && !i.hasAttribute('inert'); }", timeout=30000)
    # And then press it only if it is not already open. The dock button TOGGLES, every test in
    # this file calls this helper, and they all share one page -- so the calls alternated
    # between opening the panel and closing it, and whichever ones landed on a close spent
    # twenty seconds waiting for a `.crewcard` that was sitting right there, hidden. It was
    # `test_a_counted_string_never_renders_an_object` that drew the short straw; which test it
    # is depends only on how many are in the file above it.
    already = page.evaluate("() => { const p = document.getElementById('panel');"
                            "        return !!(p && p.classList.contains('open')"
                            "                  && p.querySelector('.crewcard')); }")
    if not already:
        page.click(".dock button[data-p=crews]")
    # Attached, not visible, plus the panel itself. A card can be correctly hidden and still
    # rendered: your own crew's card lives inside a `<details>` that Details folds away when
    # you open somebody else's crew, and it is the FIRST `.crewcard` in the panel -- so a wait
    # for visibility here hung for twenty seconds on a panel that had drawn everything right.
    page.wait_for_selector(".crewcard", state="attached", timeout=20000)
    page.wait_for_selector(".panel.open", timeout=20000)


@pytest.fixture(scope="module")
def phone(browser, world):
    """The same site, opened by a finger.

    `has_touch` is not decoration here. It is what makes `pointer: coarse` match, and the bug
    this fixture exists for is invisible to a mouse: the rule that gives a leaderboard row a
    target a finger can hit is inside that query, so a desktop context measures the old 22px
    row and passes.
    """
    ctx, pg = _page(browser, world, has_touch=True, is_mobile=True)
    yield pg
    ctx.close()


def _wait_for_champ(page):
    """The champions card is painted from two fetches, so it is not there on load."""
    page.wait_for_function(
        "() => document.querySelectorAll('#champ .cline').length > 0", timeout=30000)


def test_a_leaderboard_row_is_a_control_and_not_a_paragraph(phone):
    """Pressing a crew's name on a phone selected the name.

    Erwin: "why in the crew domination section in the phone i cant tap the name of the crew?
    is like it selects text instead". The handler was never the problem -- a scripted click on
    the same row turns the territory on and flies the map to that crew's ground. The problem
    is that the row was a `<div>` of ordinary selectable text, 22px tall, sitting on top of a
    pannable map. A finger that presses it and does not leave instantly starts the native
    selection gesture, and once a browser has committed a touch to selecting, no click
    follows.

    Three properties because it took three to be wrong at once, and each one alone still
    leaves a row that misbehaves for a different reason.
    """
    _wait_for_champ(phone)
    rows = phone.evaluate(r"""() => [...document.querySelectorAll('#champ .cline')].map(el => {
        const cs = getComputedStyle(el);
        // textContent, not innerText: the card is mid-animation often enough that
        // innerText comes back empty and the failure message names no row at all.
        return {text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40),
                select: cs.userSelect, touch: cs.touchAction,
                h: el.getBoundingClientRect().height};
    })""")
    assert rows, "the champions card rendered no rows at all"
    for row in rows:
        where = repr(row["text"])
        assert row["select"] == "none", (
            f"the row {where} is selectable text, so a press on it can become a selection "
            "instead of a tap")
        assert row["touch"] == "manipulation", (
            f"the row {where} leaves touch-action at {row['touch']}, so every tap waits out "
            "the double-tap-zoom window before it counts")
        assert row["h"] >= 30, (
            f"the row {where} is {row['h']:.1f}px tall to a finger, which is not a target")


def test_a_podium_card_does_not_hand_back_a_text_selection(phone):
    """One tap further along the same journey, and the same mistake.

    A podium card is pressed to open that rider or crew, and its name is the part a finger
    aims at. The card is large enough that the press usually wins, which is exactly why this
    would have survived another ten rounds of nobody noticing.

    Built rather than found, the way the map-tip test above builds its tip: the crews in this
    world hold no ground, so no podium is ever painted here and a test that looked for one
    looped over nothing and called it a pass. A card made by hand still meets the real
    stylesheet and the real cascade, which is the whole of what is being asked.
    """
    _wait_for_champ(phone)
    select = phone.evaluate(r"""() => {
        const el = document.createElement('div');
        el.className = 'pod p1';
        el.innerHTML = '<div class="pname">Arctic Wheelers</div>';
        document.body.appendChild(el);
        const cs = getComputedStyle(el);
        const out = {select: cs.userSelect, touch: cs.touchAction};
        el.remove();
        return out;
    }""")
    assert select["select"] == "none", (
        "a podium card is selectable text, so pressing the name on it can hand back a "
        "selection instead of opening it")
    assert select["touch"] == "manipulation", (
        f"a podium card leaves touch-action at {select['touch']}")


def _champ_on(page):
    """Which deck the switch says is showing, as a `data-go` index."""
    return page.evaluate("() => { const b = document.querySelector('#champ .cswap.on');"
                         "        return b ? b.dataset.go : null; }")


def _champ_two_decks(page):
    _wait_for_champ(page)
    page.wait_for_function("() => document.querySelectorAll('#champ .cswap').length > 1",
                           timeout=30000)


def test_the_deck_the_reader_picked_is_not_switched_back_by_the_poll(page):
    """Erwin: "when I switch to the crews info thingy in the top left, sometimes it justs
    switch back by itself".

    The card holds the deck for thirty seconds after a press, and that part works. What it
    could not survive was `pollStats`, which runs every thirty seconds of its own and calls
    `renderChampions` -- and every piece of state the rotation owned lived inside that
    function. A re-render declared `deck = 0` again, painted the default deck with no
    animation at all, and started the ordinary nine-second pace, so the press's grace was
    thrown away by a clock the reader cannot see. Pressing Crew Domination and being moved off
    it anywhere between instantly and thirty seconds later is exactly this, and "sometimes" is
    the two clocks drifting past each other.

    The same render runs on a language change and a unit change, so this was never only about
    the poll.
    """
    _champ_two_decks(page)
    first = _champ_on(page)
    picked = page.evaluate("""() => {
        const b = [...document.querySelectorAll('#champ .cswap')]
          .find(x => !x.classList.contains('on'));
        b.click();
        return b.dataset.go;
      }""")
    page.wait_for_timeout(600)                 # the tear swaps the content at its midpoint
    assert _champ_on(page) == picked, (
        f"the switch did not change the deck at all: still on {first}")
    page.evaluate("() => pollStats()")         # the real poll, not a stand-in for one
    page.wait_for_timeout(400)
    assert _champ_on(page) == picked, (
        f"the poll put the card back on deck {_champ_on(page)} after the reader asked for "
        f"deck {picked}")
    state = page.evaluate("() => { const ch = document.getElementById('champ');"
                          "        return {grace: (ch._grace || 0) - Date.now(),"
                          "                rot: !!ch._rot}; }")
    assert state["grace"] > 20000, (
        f"the press's thirty-second grace came out of the render with {state['grace']}ms left")
    assert not state["rot"], (
        "the render armed the ordinary nine-second swap while the press's grace was still "
        "running, so the deck would be taken away nine seconds later")
    page.evaluate("""(go) => {
        const b = document.querySelector('#champ .cswap[data-go="' + go + '"]');
        if (b) b.click();
      }""", first)
    page.wait_for_timeout(600)


def test_the_poll_does_not_start_the_swap_under_a_resting_pointer(page):
    """The other half of the same loss. The swap is paused while a pointer is on the card so
    it never changes under somebody reading it, and that pause was a cleared interval and
    nothing else -- no record that the pointer was still there. So any render during the read,
    and the poll is one every thirty seconds, started the pace again under the pointer.
    """
    _champ_two_decks(page)
    page.dispatch_event("#champ", "pointerenter")
    page.wait_for_timeout(100)
    page.evaluate("() => pollStats()")
    page.wait_for_timeout(400)
    armed = page.evaluate("() => { const ch = document.getElementById('champ');"
                          "        return !!(ch._rot || ch._rotWait); }")
    assert not armed, (
        "the poll re-armed the swap while the pointer was resting on the card, so the deck "
        "changes under the reader")
    page.dispatch_event("#champ", "pointerleave")
    page.wait_for_timeout(100)
    back = page.evaluate("() => { const ch = document.getElementById('champ');"
                         "        return !!(ch._rot || ch._rotWait); }")
    assert back, "taking the pointer off the card did not start the swap again"


def test_the_panel_opens_at_all(page):
    """The floor. If this fails nothing below it means anything."""
    _open_panel(page)
    assert page.query_selector(".crewcard") is not None


def test_a_rendered_control_is_a_bound_control(page):
    """The waiting state rendered a filter and a toggle and bound neither.

    Generalised past that one card: every control the panel renders with an id this feature
    binds by must actually have a handler. A dead control is worse than an absent one -- the
    rider is told they can do something and then nothing happens.
    """
    _open_panel(page)
    bound = page.evaluate("""() => {
        const out = {};
        const f = document.getElementById('cj-filter');
        const m = document.getElementById('cj-more');
        if (f) out.filter = typeof f.oninput === 'function';
        if (m) out.toggle = typeof m.onclick === 'function';
        return out;
    }""")
    for name, ok in bound.items():
        assert ok, (f"the join list's {name} is rendered but has no handler -- it is a control "
                    "that looks live and does nothing")


def test_a_stranger_can_read_the_crews_without_signing_in(page):
    """Signed out, the only crew list on the page was the board.

    `render` builds the left column as `me.paired ? own : signInHTML()`, and every crew list in
    the feature -- `joinHTML`, `rivalsHTML` -- is inside `own`. So a visitor with no pass got
    the sign-in card and the standings, and the standings have rows only for crews that hold
    ground. A tester reading production, where two crews exist and both are ranked, saw first
    and second place and nothing after them and asked whether the list is "only with people
    that have been signed". It was.

    The list rendered here is the read-only one -- the same rows a member sees under Other
    crews, which already handle a reader with no crew of their own. No join buttons: joining
    needs a pass, and a row of controls that cannot work is the dead-control mistake this file
    exists to catch.
    """
    _open_panel(page)
    page.wait_for_selector(".crewsign", timeout=20000)
    out = page.evaluate("""async () => {
        const r = await fetch('/api/v1/crews?limit=60');
        const b = await r.json();
        const cards = [...document.querySelectorAll('#crewpanel .crewcard')];
        const list = document.querySelector('.crewrivals');
        const h = list ? list.querySelector('h4') : null;
        return {
          mine: !!document.querySelector('.crewmine-wrap'),
          known: (b.crews || []).map(c => c.slug).sort(),
          listed: list
            ? [...list.querySelectorAll('.crewrivrow')].map(x => x.dataset.rival).sort()
            : null,
          heading: h ? h.textContent.trim() : null,
          joins: list ? list.querySelectorAll('button[data-join], .crewjoin').length : 0,
          signFirst: !!(cards.length && cards[0].classList.contains('crewsign')),
          lit: list ? list.querySelectorAll('[data-lit]').length : 0,
        };
      }""")
    assert not out["mine"], "this page is signed in, so it cannot answer for a stranger"
    assert out["known"], "the world has no crews, so this test is not asking anything"
    assert out["listed"] is not None, (
        "signed out there is no crew list on the page at all, only the board -- which has no "
        "row for a crew that holds nothing")
    assert out["listed"] == out["known"], (
        f"the list shows {out['listed']} and the site has {out['known']}")
    assert out["heading"] == EN["crew.rivals.all"], (
        f"the list signed out is headed {out['heading']!r}; with no crew of your own there is "
        "nothing for the others to be other than")
    assert out["heading"] != EN["crew.rivals.h"], "the signed-out heading is the member's one"
    assert out["joins"] == 0, (
        f"{out['joins']} join controls are rendered to somebody who cannot join")
    assert out["signFirst"], (
        "the first card a stranger lands on is no longer the way in")
    assert out["lit"], "no row in the signed-out list does anything when pressed"


def test_the_map_tip_is_the_same_size_wherever_it_sits(page):
    """`placeTip` measured a box that shrink-to-fit had collapsed.

    An absolutely positioned box with only a `max-width` sizes against the room to its right,
    so near an edge it measured 36px instead of 210 and the clamp reasoned about a box that no
    longer existed: the tip rendered as a 626px-tall ribbon with one character per line.
    """
    _open_panel(page)
    widths = page.evaluate("""() => {
        const host = document.querySelector('.maplibregl-canvas-container') || document.body;
        const seen = new Set();
        for (const left of [0, 100, 195, 300, 378, 389]) {
            const el = document.createElement('div');
            el.className = 'crewtip b1';
            el.innerHTML = '<b>Kjenna few streets</b><span>Rival Crew</span>'
                         + '<span>one lap <i>0.2 km</i></span>';
            el.style.left = left + 'px';
            el.style.top = '300px';
            host.appendChild(el);
            seen.add(Math.round(el.getBoundingClientRect().width));
            el.remove();
        }
        return [...seen];
    }""")
    assert len(widths) == 1, (
        f"the map tip takes {len(widths)} different widths depending on where it sits "
        f"({sorted(widths)}). Anything measuring it will measure the wrong number.")


def test_a_counted_string_never_renders_an_object(page):
    """A count shadowed by an array printed "[object Object]" seven times.

    Nothing in the panel should ever show a stringified object or an unsubstituted placeholder.
    Both are the signature of a value that was not what its author thought it was.
    """
    _open_panel(page)
    text = page.inner_text("#pbody")
    assert "[object" not in text, "the panel is rendering a stringified object"
    assert "undefined" not in text, "the panel is rendering the word undefined"
    assert "NaN" not in text, "the panel is rendering NaN"
    # An un-substituted i18n placeholder reaches a rider as a literal brace.
    assert "{" not in text and "}" not in text, (
        "the panel is rendering a literal brace, so a placeholder was not substituted")


def test_nothing_the_panel_renders_overflows_it(page):
    """Several rounds were spent on boxes that ran past the card they sat in."""
    _open_panel(page)
    bad = page.evaluate("""() => {
        const doc = document.documentElement;
        const pb = document.querySelector('#pbody');
        return { page: doc.scrollWidth - doc.clientWidth,
                 panel: pb ? pb.scrollWidth - pb.clientWidth : 0 };
    }""")
    assert bad["page"] <= 0, f"the page scrolls sideways by {bad['page']}px at 390"
    assert bad["panel"] <= 0, f"the panel scrolls sideways by {bad['panel']}px at 390"


def test_a_crew_emblem_is_positioned_the_way_maplibre_needs(page):
    """Every crew emblem was drawn low, by the stacked height of the markers above it.

    `.crewemb` set `position: relative`, and crews.css loads after maplibre-gl.css, so at
    equal specificity it beat MapLibre's own `.maplibregl-marker { position: absolute; top: 0;
    left: 0 }`. Every marker was then laid out in NORMAL FLOW inside the canvas container and
    offset by its transform from there instead of from the container's origin. On Jonah's turf
    the map projected his two badges to y=475 and y=185 and the browser painted them at 531
    and 275 -- 56px and 90px off their own ground, by different amounts, growing with the
    number of crews on the map.

    `declutterEmblems` compares `map.project` positions, so it was hiding badges by where they
    should have been rather than where they were, and a rotation made them disappear. Erwin:
    "see what happens now when I rotate, they go away".

    Built rather than found, and still worth building. What this one asks is a question about
    the cascade on its own -- does anything loaded after MapLibre take a marker out of absolute
    positioning -- and a bare element answers it against the real stylesheets in the real order
    whether or not any crew happens to hold ground. `world` rides a block of squares now, so
    `test_an_emblem_is_painted_where_its_own_transform_puts_it` checks the consequence on a
    real badge; this checks the cause, and names the property to put back.
    """
    _open_panel(page)
    got = page.evaluate("""() => {
        const el = document.createElement('div');
        el.className = 'crewemb maplibregl-marker maplibregl-marker-anchor-center';
        (document.querySelector('.maplibregl-canvas-container') || document.body)
            .appendChild(el);
        const cs = getComputedStyle(el);
        const out = {position: cs.position, top: cs.top, left: cs.left};
        el.remove();
        return out;
    }""")
    assert got["position"] == "absolute", (
        f"a crew emblem computes to position:{got['position']}, so its transform offsets it "
        "from wherever normal flow put it rather than from the map's origin")
    assert got["top"] == "0px" and got["left"] == "0px", (
        f"a crew emblem starts at {got['left']},{got['top']} rather than the map's origin, so "
        "every projected position is shifted by that much")


def test_the_panel_reports_no_console_errors(page):
    """The `esc is not defined` round: a ReferenceError in a render path emptied the crew card
    for every crew with contributors, and every guard in the suite stayed green."""
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.reload(wait_until="domcontentloaded")
    _open_panel(page)
    page.wait_for_timeout(1500)
    # Connection noise from the harness's own fixtures is not this feature's fault.
    real = [e for e in errors if "ERR_CONNECTION" not in e and "favicon" not in e]
    assert not real, "the panel logged errors: " + " | ".join(real[:4])


@pytest.fixture(scope="module")
def mapped(browser, world):
    """A page of its own, with the MapLibre map captured on the way past.

    `crews.js` keeps the map in a module variable and exposes `openCrew`/`setOverlay` and
    nothing else, which is right -- a marker's bearing is not a thing the site needs to publish.
    So the map is taken the only way that costs the shipped code nothing: an init script
    intercepts the one assignment to `window.EUCCrews` and wraps `init`, which the page calls
    with the map from inside `map.on("load")`.

    Its own context because the test below rotates and pitches the camera, and the shared
    `page` is module-scoped: leaving a 60-degree bearing behind would hand every test after it
    a map nobody asked for.
    """
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script("""
      (() => {
        let held;
        Object.defineProperty(window, 'EUCCrews', {
          configurable: true,
          get() { return held; },
          set(v) {
            const real = v && v.init;
            if (real) v.init = function (m) { window.__map = m; return real.apply(this, arguments); };
            held = v;
          },
        });
      })();
    """)
    pg = ctx.new_page()
    pg.goto(world["base"], wait_until="domcontentloaded")
    yield pg
    ctx.close()


def _emblems(page):
    """Every badge on the map, with where its own transform says it is and where it is."""
    return page.evaluate(r"""() => [...document.querySelectorAll('.crewemb')].map(el => {
        const t = el.style.transform || '';
        const px = [...t.matchAll(/-?[\d.]+px/g)].map(m => parseFloat(m[0]));
        const r = el.getBoundingClientRect();
        const rot = /rotateZ\((-?[\d.]+)deg\)/.exec(t);
        const pit = /rotateX\((-?[\d.]+)deg\)/.exec(t);
        return {
          transform: t,
          said: px.length >= 2 ? [px[0], px[1]] : null,
          drawn: [r.left + r.width / 2, r.top + r.height / 2],
          rotateZ: rot ? parseFloat(rot[1]) : null,
          rotateX: pit ? parseFloat(pit[1]) : null,
          hidden: getComputedStyle(el).display === 'none' || el.style.opacity === '0',
        };
      })""")


def _show_ground(page):
    page.wait_for_function("() => !!window.__map", timeout=30000)
    _open_panel(page)
    page.evaluate("() => window.EUCCrews.setOverlay(true)")
    page.wait_for_selector(".crewemb", timeout=20000)


def test_an_emblem_is_painted_where_its_own_transform_puts_it(mapped):
    """The gap the cascade test above had to leave, closed: a real badge on real ground.

    `world` now rides a 3x3 block of squares, so `buildEmblems` actually runs here. The
    question is the one that found the bug on production -- MapLibre writes the projected
    position into the element's `transform`, and if anything takes the marker out of absolute
    positioning that transform offsets from wherever normal flow put it instead. The badge
    then sits beside its own ground by the stacked height of the markers above it.
    """
    _show_ground(mapped)
    got = _emblems(mapped)
    assert got, "no emblem was painted, so this test is not asking anything"
    for e in got:
        assert e["said"], f"a marker carries no translate at all: {e['transform']!r}"
        dx = abs(e["drawn"][0] - e["said"][0])
        dy = abs(e["drawn"][1] - e["said"][1])
        assert dx < 2 and dy < 2, (
            f"a badge whose transform says {e['said']} is painted at {e['drawn']} "
            f"({dx:.0f},{dy:.0f} px off its own ground)")


def test_an_emblem_turns_with_the_land_under_it(mapped):
    """Erwin: "logos should stick in biggest patches of land in the center 2x2 minimum, and
    rotate with them". Sticking was the `position` fix; turning is this.

    A marker is `rotationAlignment: 'viewport'` by default, so the badge held the same angle
    whatever the compass did. `'map'` makes MapLibre write `rotateZ(-bearing)` into the same
    transform that carries the position, so the badge turns with the ground it labels.

    And `pitchAlignment`, which is the trap: a Marker given no `pitchAlignment` does not
    default to `'viewport'`, it defaults to whatever `rotationAlignment` is. Turning the badge
    with the compass would therefore also have laid it flat on the ground plane the moment
    anybody tilted the map -- `rotateX(60deg)`, a badge squashed into a 40%-tall ellipse. Both
    halves are asserted because one line fixes both and one line can lose both.
    """
    _show_ground(mapped)
    mapped.evaluate("() => window.__map.rotateTo(60, {duration: 0})")
    mapped.wait_for_timeout(250)
    assert mapped.evaluate("() => Math.round(window.__map.getBearing())") == 60
    for e in _emblems(mapped):
        assert e["rotateZ"] is not None, (
            f"the badge's transform carries no rotation at a 60 degree bearing: "
            f"{e['transform']!r} -- it is pinned to the viewport, not to the land")
        assert abs(e["rotateZ"] - -60) < 1, (
            f"the map is at bearing 60 and the badge is turned {e['rotateZ']} degrees, so it "
            "is not following the ground")

    # Tilted as well as turned: the pitchAlignment half.
    mapped.evaluate("() => window.__map.easeTo({pitch: 55, duration: 0})")
    mapped.wait_for_timeout(250)
    assert mapped.evaluate("() => Math.round(window.__map.getPitch())") == 55
    for e in _emblems(mapped):
        assert not e["rotateX"], (
            f"at 55 degrees of pitch the badge is rotated {e['rotateX']} degrees about X, so "
            "it is being painted into the ground plane instead of facing the reader")

    mapped.evaluate("() => window.__map.easeTo({bearing: 0, pitch: 0, duration: 0})")


@pytest.fixture(scope="module")
def solo_leader(browser, world):
    """The panel, opened by the only rider in a crew.

    `world` makes `brw-lead` the leader of Browser Crew and puts nobody else in it, which is
    the shape this is about: for a crew of one, Leave crew ends the crew. Signed in the way a
    phone signs in -- a pairing code confirmed server-side, then the poll endpoint navigated
    to once so the browser is handed the same `crew_session` cookie it would get from a scan.
    """
    from database import SessionLocal
    from services import pairing

    db = SessionLocal()
    try:
        p = pairing.start(db, purpose="rider")
        pairing.confirm(db, p["code"], "brw-lead")
    finally:
        db.close()
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    pg.goto(f"{world['base']}/api/v1/pair/poll?token={p['token']}",
            wait_until="domcontentloaded")
    pg.goto(world["base"], wait_until="domcontentloaded")
    yield pg
    ctx.close()


def test_the_last_rider_in_a_crew_has_to_type_its_name_to_leave(solo_leader):
    """JonahOnEUC left his own crew by mistake and it was folded by the press.

    Erwin: "there was a crew user who by mistake exited his own crew and then it got deleted
    ... can we prevent this with a stronger confirmation? since is the last one to leave".

    For a crew of one, Leave crew is Disband with a week's cooldown attached, and the card
    said the opposite: Leave sat in the ordinary action row as a plain ghost button under a
    tooltip promising "the rides you already did stay on the map", while Disband sat below a
    rule, under a heading, behind a gate that makes you type the crew's name. The confirm
    text did say the crew would end -- but this file's own note on Disband is that "a guard
    you meet after pressing is not the same as a button that looks like what it does", and
    the gate is what that note put there.

    So the last one out meets the same gate. Deliberately stopping short of completing it:
    finishing the leave would fold the crew the rest of this module renders from.
    """
    page = solo_leader
    _open_panel(page)
    # `Manage` is a collapsed <details>, so the button exists before it is visible.
    page.wait_for_selector("#cm-leave", state="attached", timeout=20000)
    page.evaluate("() => { const d = document.querySelector('details.crewmanage');"
                  "        if (d) d.open = true; }")
    page.click("#cm-leave")
    page.wait_for_timeout(400)
    assert page.query_selector("#crewask-in") is not None, (
        "the last rider in a crew was asked a yes/no question to end it, the same question a "
        "member of five gets to walk away from one")

    # And it has to be the right name. An empty box must not pass for one.
    page.click("#crewask-y")
    page.wait_for_timeout(400)
    assert page.query_selector("#cm-leave") is not None, "the crew card is still there"
    assert page.evaluate("() => { const m = document.querySelector('.crewaskmsg');"
                         "        return !!(m && !m.hidden && m.textContent.trim()); }"), (
        "pressing the confirm on an empty box said nothing")

    page.click("#crewask-n")
    page.wait_for_timeout(200)
    assert page.query_selector("#crewask-in") is None, "Cancel puts the gate away"


def test_the_last_rider_is_not_told_their_rides_stay_on_the_map(solo_leader):
    """The other half, and the part that was actively misleading. `crew.tip.leave` reads "You
    stop riding for this crew. The rides you already did stay on the map." -- true for a
    member of five, false for the only member, whose crew and whose map go with them."""
    page = solo_leader
    _open_panel(page)
    page.wait_for_selector("#cm-leave", state="attached", timeout=20000)
    title = page.get_attribute("#cm-leave", "title") or ""
    assert title, "the button explains itself in plain words, or it used to"
    assert "stay on the map" not in title.lower(), (
        f"the only rider in a crew is told {title!r}, and nothing of theirs stays on the map")


# --- "Other crews", and the fold under it -------------------------------------------------
#
# This list had no test of any kind. It rendered facts and a bare `aria-hidden` chevron, and
# the row scrolled the panel to that crew's row on the board. Erwin read the chevron the way
# anybody would: "there is a little arrow that kinda tells you could actually see more info
# inside no? but it doesnt expand to anything? why?" -- and then asked for what it should have
# been: "can we have some extra info? and maybe a button to center on the crew and do the
# animation with the highlight like in the crew domination section or in who owns the streets".
#
# So the row carries the facts the jump existed to show, the title lights that crew's ground,
# and the fold opens into who rode for them. All three get measured here, on the real page.


@pytest.fixture(scope="module")
def rivals(browser, world):
    """The panel as a member in good standing sees it, with a rival worth looking at.

    `world` gives Browser Crew ground in Oslo and leaves Rival Crew holding nothing, which is
    the one shape these tests cannot use: a crew with no ground has no position on the board,
    no patch to fly to and nobody on its contributors list, so every assertion below would be
    checking that nothing renders. So Rival Crew gets a block of Copenhagen and the rider who
    earned it.

    Deliberately a different city. Two patches six hundred kilometres apart cannot overlap, so
    the badge declutter has nothing to hide and the emblem tests above still see every marker
    they saw before -- and a camera that was framed on Oslo and ends up over Copenhagen has
    demonstrably gone where it was sent.

    The map is captured the same way `mapped` captures it, because the camera is the only
    honest witness to "center on the crew" and `crews.js` publishes `openCrew`/`setOverlay`
    and nothing else.
    """
    from database import SessionLocal
    from services import pairing

    db = SessionLocal()
    try:
        theirs = crews.membership(db, "brw-rival")
        assert theirs is not None, "world did not build Rival Crew"
        _give_ground(db, "brw-rival", theirs.clan_id, lat=55.6761, lon=12.5683)
        # And a smaller outpost 250km up the coast, in Gothenburg. Highlight lights every
        # square a crew holds and frames ONE patch, and a crew with a single patch cannot
        # tell those two things apart: 9 squares held, 9 lit, 9 framed, and a version that
        # lights only what it frames passes. With the outpost the crew holds 13 and the
        # camera still belongs over the 9.
        _give_ground(db, "brw-rival", theirs.clan_id, lat=57.7089, lon=11.9746,
                     rows=2, cols=2, tag="-out")
        # And one crew holding nothing, which is the row that must NOT be pressable: `blink`
        # frames a crew's biggest patch and returns silently when there is no patch, so a
        # bound row here would be a control that does nothing -- on exactly the rows where
        # nothing is the honest answer.
        if db.get(models.Rider, "brw-ghost") is None:
            db.add(models.Rider(store_id="brw-ghost", display_name="brw-ghost",
                                platform="google_play", flag="NO"))
            db.commit()
            # `can_found` wants one validated ride before anybody may start a crew, so the
            # seed trip is part of building a rider here -- the same pair `world` adds above.
            # No track on it, so it earns no ground, which is the whole point of this crew.
            db.add(models.Trip(trip_uuid="brw-seed-brw-ghost", rider_store_id="brw-ghost",
                               distance_km=5.0, validation_status="validated",
                               start_utc=models.utcnow() - timedelta(days=2),
                               end_utc=models.utcnow() - timedelta(days=2)))
            db.commit()
        if crews.membership(db, "brw-ghost") is None:
            crews.create(db, "brw-ghost", "Ghost Crew", "", join_policy="open")
            db.commit()
        p = pairing.start(db, purpose="rider")
        pairing.confirm(db, p["code"], "brw-lead")
    finally:
        db.close()
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script("""
      (() => {
        let held;
        Object.defineProperty(window, 'EUCCrews', {
          configurable: true,
          get() { return held; },
          set(v) {
            const real = v && v.init;
            if (real) v.init = function (m) { window.__map = m; return real.apply(this, arguments); };
            held = v;
          },
        });
      })();
    """)
    pg = ctx.new_page()
    pg.goto(f"{world['base']}/api/v1/pair/poll?token={p['token']}",
            wait_until="domcontentloaded")
    pg.goto(world["base"], wait_until="domcontentloaded")
    pg.wait_for_function("() => !!window.__map", timeout=30000)
    yield pg
    ctx.close()


def _rivals_open(page):
    """The panel, open, with the other-crews list rendered.

    Every test here calls it: `_open_panel` is idempotent, and the Highlight test below closes
    the panel on purpose, so nothing may assume it is still open.
    """
    _open_panel(page)
    page.wait_for_selector(".crewrivrow", timeout=20000)


def _rival_row(page):
    return page.evaluate("""() => {
        const row = document.querySelector('.crewrivrow');
        if (!row) return null;
        const q = s => row.querySelector(s);
        const head = q('.crewrivhead');
        const card = row.closest('.crewcard');
        const hr = head ? head.getBoundingClientRect() : null;
        const cr = card ? card.getBoundingClientRect() : null;
        return {
          slug: row.dataset.rival || '',
          name: q('b') ? q('b').textContent.trim() : '',
          stand: q('.crewstand') ? q('.crewstand').textContent.trim() : null,
          sub: q('.crewrivsub') ? q('.crewrivsub').textContent.trim() : '',
          oldChevron: !!q('.crewrivgo'),
          headRole: head ? head.getAttribute('role') : null,
          headLabel: head ? (head.getAttribute('aria-label') || '') : '',
          fold: q('.crewwho[data-who]') ? {
            label: q('.crewwho[data-who]').textContent.trim(),
            expanded: q('.crewwho[data-who]').getAttribute('aria-expanded'),
            controls: q('.crewwho[data-who]').getAttribute('aria-controls'),
          } : null,
          panelId: q('.crewwhopan:not(.crewpatpan)') ? q('.crewwhopan:not(.crewpatpan)').id : null,
          overflowsCard: !!(hr && cr && hr.right > cr.right + 1),
        };
      }""")


def test_a_rival_row_carries_the_facts_it_used_to_send_you_to_the_board_for(rivals):
    """Position, movement and the ground the ranking is actually made of.

    The row used to print `tiles` -- everything held -- inside `crew.board.inp`'s "{a} in {b}",
    so a crew the board leads with as "46 in one patch" read "58 squares in 3 patches" here:
    two answers to one question on one screen, with the figure the ranking is made of being
    the one missing. And the only way to see where that crew stood was to press the row and be
    scrolled to a second copy of all of it.
    """
    _rivals_open(rivals)
    row = _rival_row(rivals)
    assert row, "no row in Other crews, so this test is not asking anything"
    assert row["name"], "the row does not name the crew"
    assert not row["oldChevron"], (
        "the bare aria-hidden chevron is still being rendered; it is what was mistaken for a "
        "disclosure in the first place")
    assert row["stand"], (
        "the row says nothing about where that crew stands, which is the fact the jump to the "
        "board existed to show")
    assert any(ch.isdigit() for ch in row["stand"]), (
        f"the standing reads {row['stand']!r} and carries no position")
    assert row["sub"], "the row says nothing about what that crew holds"
    assert any(ch.isdigit() for ch in row["sub"]), f"the ground line reads {row['sub']!r}"
    assert not row["overflowsCard"], (
        "the title line is pushed past the right edge of its own card")


def test_a_rival_name_is_a_control_that_says_what_it_does(rivals):
    """`pressable` is given the crew's name AND the action. A control labelled only "Rival
    Crew" tells a screen reader there is a button by that name and nothing about why."""
    _rivals_open(rivals)
    row = _rival_row(rivals)
    assert row["headRole"] == "button", (
        f"the title carries role={row['headRole']!r}, so the one thing on the row that does "
        "something is not announced as a control")
    assert row["name"].lower() in row["headLabel"].lower(), (
        f"the control is labelled {row['headLabel']!r} and does not name the crew")
    assert row["headLabel"].strip() != row["name"].strip(), (
        f"the control is labelled {row['headLabel']!r}, which names the crew and not the act")


def test_the_fold_says_where_it_goes_before_it_is_pressed(rivals):
    """The original complaint was an arrow that opened something without saying what. The fold
    still says it; the words are the count it is about.

    Erwin: "I think we put a link in X riders and a link in X patches instead of using the
    'Best riders' and 'Biggest patches' buttons, so the whole thing is streamlined ... just
    that when everything is collapsed looks more tidy". `4 riders` names the list as well as
    `Best riders` did and the row already had to print it, so the label was one line of chrome
    per crew saying what the line above it said. It is still a real button carrying
    `aria-expanded` and `aria-controls`, which is what a disclosure is.
    """
    _rivals_open(rivals)
    row = _rival_row(rivals)
    assert row["fold"], "there is no fold under the row at all"
    assert any(ch.isdigit() for ch in row["fold"]["label"]), (
        f"the fold reads {row['fold']['label']!r}, which is not the count the row prints")
    assert row["fold"]["label"] in row["sub"], (
        f"the fold's label {row['fold']['label']!r} is not part of the facts line "
        f"{row['sub']!r}: it is still a line of its own")
    assert row["fold"]["expanded"] == "false", (
        f"the fold reports aria-expanded={row['fold']['expanded']!r} while shut")
    assert row["fold"]["controls"] and row["fold"]["controls"] == row["panelId"], (
        f"the fold points at {row['fold']['controls']!r} and the panel it opens is "
        f"{row['panelId']!r}")


def test_a_count_on_the_facts_line_is_pressable_where_a_thumb_lands(rivals):
    """What the source cannot answer: whether an 11px word in a line of five facts is a
    control a finger can hit, and whether it still looks like one before it is hovered.

    `.click()` bypasses hit testing, so the number under the thumb is measured with
    `elementFromPoint` -- the row carries other controls, and the one directly above this line
    is the crew's name, which flies the map.
    """
    _rivals_open(rivals)
    seen = rivals.evaluate(r"""() => {
        const row = document.querySelector('.crewrivrow');
        const sub = row.querySelector('.crewrivsub');
        const who = row.querySelector('.crewwho[data-who]');
        const lab = who.querySelector('span');
        // NOT a neighbouring fact: the facts line is a flex row and `align-items` defaults to
        // stretch, so every item on it reports the height of the tallest -- the control's own
        // height, which made that comparison read 19 against 19 and pass whatever the padding
        // was. The label inside the button is the thing the padding is added to.
        // The panel scrolls and the first row can be below the fold, where `elementFromPoint`
        // answers null for every coordinate -- which reads exactly like a control nothing can
        // press. Put it on screen first, then measure where it actually is.
        who.scrollIntoView({ block: 'center' });
        const r = who.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {
          inLine: sub.contains(who),
          label: who.textContent.replace(/\s+/g, ' ').trim(),
          // The line AND the style: with the rule gone the style still computes to
          // 'solid', which is the default for text that carries no decoration at all.
          underline: getComputedStyle(lab).textDecorationLine + ' '
                   + getComputedStyle(lab).textDecorationStyle,
          colour: getComputedStyle(who).color,
          chevron: !!who.querySelector('i'),
          chevronInLabel: !!lab.querySelector('i'),
          hit: hit ? (who.contains(hit) ? 'control' : (hit.className || hit.tagName))
                   : 'nothing at ' + Math.round(r.left) + ',' + Math.round(r.top),
          box: [Math.round(r.width), Math.round(r.height)],
          labBox: Math.round(lab.getBoundingClientRect().height),
          oldBoxes: row.querySelectorAll('.crewwhobox').length,
          facts: sub.querySelectorAll('.crewfact').length,
          rowHeight: Math.round(row.getBoundingClientRect().height),
          // Every fact in the list against the card it sits in. The line is a flex row that
          // wraps now, and a fact is `nowrap`, so the thing that can go wrong at 390 is one
          // long fact running past the edge rather than the line failing to break.
          worstOverflow: Math.round(Math.max(...[...document.querySelectorAll('.crewrivrow')]
            .flatMap(rw => {
              const edge = rw.closest('.crewcard').getBoundingClientRect().right;
              return [...rw.querySelectorAll('.crewfact')]
                .map(f => f.getBoundingClientRect().right - edge);
            }))),
        };
      }""")
    assert seen["inLine"] and not seen["oldBoxes"], (
        f"the control is not inside the facts line (oldBoxes={seen['oldBoxes']}), so nothing "
        "was streamlined: the row still carries a line per fold")
    assert seen["facts"] >= 3, (
        f"the facts line holds {seen['facts']} facts, so this is not the line the counts "
        "were meant to join")
    assert seen["hit"] == "control", (
        f"a press in the middle of the count lands on {seen['hit']!r}: the number is "
        "underlined like a control and something else answers for it")
    assert seen["box"][1] >= seen["labBox"] + 3, (
        f"the control is {seen['box'][1]}px tall around a {seen['labBox']}px label. The "
        "padding on `.crewwho` is what buys an 11px word something to be pressed with, and "
        "the label is the only thing on that line whose height the padding is added to")
    assert seen["underline"] == "underline dotted", (
        f"the label's decoration is {seen['underline']!r}. A dotted underline is what "
        "separates a fact that opens in place from the crew's name above it, which goes "
        "somewhere, and from the plain facts beside it, which do nothing")
    assert seen["worstOverflow"] <= 1, (
        f"a fact on the line runs {seen['worstOverflow']}px past the right edge of its own "
        "card at this width. The facts wrap between themselves and never inside one, so a "
        "single fact wider than the card is the only way this happens")
    assert seen["chevron"] and not seen["chevronInLabel"], (
        "the chevron is missing, or it is inside the underlined label, which draws a line "
        "under the one glyph on the row that turns")


def test_the_fold_opens_into_who_rode_for_that_crew(rivals):
    """And asks about that crew once, rather than fetching every crew on the chance.

    `/crews/{slug}` has served `contributors` for any crew all along -- "a contributors list
    is public", says the server -- and nothing in the panel had ever read it for a crew that
    was not the reader's own.
    """
    page = rivals
    _rivals_open(page)
    row = _rival_row(page)
    slug = row["slug"]
    hits = []
    page.on("request",
            lambda r: hits.append(r.url) if ("/crews/" + slug) in r.url else None)

    page.click(".crewrivrow .crewwho[data-who]")
    page.wait_for_selector(".crewwhopan .crewcrow", timeout=20000)
    got = page.evaluate("""() => {
        const pan = document.querySelector('.crewwhopan:not(.crewpatpan)');
        const rows = [...pan.querySelectorAll('.crewcrow')];
        const txt = (r, s) => { const e = r.querySelector(s); return e ? e.textContent.trim() : ''; };
        return {
          hidden: pan.hidden,
          expanded: document.querySelector('.crewwho[data-who]').getAttribute('aria-expanded'),
          names: rows.map(r => txt(r, '.crewcname')),
          kms: rows.map(r => txt(r, '.crewckm')),
          spinner: !!pan.querySelector('.crewwhospin'),
          busy: pan.getAttribute('aria-busy'),
          heading: (() => {
            const h = pan.querySelector('.crewcontrib h4');
            return h ? { text: h.textContent.trim(),
                         shown: getComputedStyle(h).display !== 'none' } : null;
          })(),
        };
      }""")
    assert got["expanded"] == "true", "the fold did not report itself open"
    assert not got["hidden"], "the fold is open and its panel is still hidden"
    assert got["names"] and got["names"][0], (
        "the fold opened and named nobody, on a crew whose ground was earned by a rider this "
        "fixture created")
    assert any(ch.isdigit() for ch in got["kms"][0]), (
        f"the rider is listed as {got['kms'][0]!r}, with no distance against their name")
    assert not got["spinner"] and not got["busy"], (
        "the loading state is still on screen after the rows arrived")
    # The heading used to be `display: none` here, because the button above it said "Best
    # riders". The button says "4 riders" now, and the two are not the same claim: this list
    # holds the five who rode the most, while the count is the whole membership. A reader who
    # presses "7 riders" and is shown four names has been told something untrue unless the
    # panel says which four.
    assert got["heading"] and got["heading"]["shown"] and got["heading"]["text"], (
        f"the panel is headed {got['heading']!r}: the words that named this list moved onto "
        "the count, and nothing took their place")
    assert len(hits) == 1, f"one fold opened and {len(hits)} requests went out: {hits}"

    # Shut and open again. The answer is kept, so a reader comparing two rivals pays once.
    page.click(".crewrivrow .crewwho[data-who]")
    page.wait_for_timeout(150)
    assert page.evaluate("() => document.querySelector('.crewwhopan:not(.crewpatpan)').hidden") is True, (
        "pressing the open fold did not shut it")
    page.click(".crewrivrow .crewwho[data-who]")
    page.wait_for_timeout(250)
    assert page.evaluate("() => !!document.querySelector('.crewwhopan .crewcrow')"), (
        "reopening the fold lost the list")
    assert len(hits) == 1, (
        f"the fold was opened twice and asked the server {len(hits)} times; the first answer "
        "was thrown away")


def test_where_a_crew_stands_is_not_cut_off_in_any_language(rivals):
    """The measurement `.crewstand`'s entry in `test_crews_clipping.REVIEWED` promises.

    That rule is `white-space: nowrap`, which is the text-cutting trap this file has been
    caught by twice, once in eleven locales. It is defensible here only because none of the
    content is prose: the longest ordinal across all nineteen locales is four characters, and
    the arrow is one glyph followed by a number bounded by the length of the board itself. So
    rather than taking that on trust, every locale's real worst case is written into a real
    row and measured.
    """
    page = rivals
    _rivals_open(page)
    worst = page.evaluate("""(cases) => {
        const el = document.querySelector('.crewstand');
        const head = document.querySelector('.crewrivhead');
        const card = head.closest('.crewcard');
        const was = el.textContent;
        const out = cases.map(c => {
          el.textContent = c.text;
          void el.offsetWidth;
          const hr = head.getBoundingClientRect(), cr = card.getBoundingClientRect();
          return {
            loc: c.loc, text: c.text,
            clipped: el.scrollWidth > el.clientWidth + 1,
            over: Math.round(hr.right - cr.right),
          };
        });
        el.textContent = was;
        return out;
      }""", WORST_ORDINALS)
    assert len(worst) == 19, f"{len(worst)} locales were measured, not 19"
    bad = [w for w in worst if w["clipped"]]
    assert not bad, ("the standing is cut off in: "
                     + ", ".join(f"{w['loc']} ({w['text']!r})" for w in bad))
    spill = [w for w in worst if w["over"] > 1]
    assert not spill, ("the title line is pushed past the edge of its own card in: "
                       + ", ".join(f"{w['loc']} by {w['over']}px" for w in spill))


def test_a_crew_with_no_ground_is_not_a_control(rivals):
    """The dead-button rule this file records itself breaking twice.

    `blink` reads the crew's squares out of the territory payload and returns without doing
    anything when there are none, so a bound row on a crew that holds nothing would be a
    control whose entire effect is nothing -- and it would be bound on precisely the rows
    where a reader is most likely to press it to find out what the crew has. Those rows keep
    the name, the riders and the crew's own words. They are just not buttons.
    """
    _rivals_open(rivals)
    rows = rivals.evaluate("""() => [...document.querySelectorAll('.crewrivrow')].map(r => {
        const head = r.querySelector('.crewrivhead');
        return {
          name: r.querySelector('b') ? r.querySelector('b').textContent.trim() : '',
          sub: r.querySelector('.crewrivsub') ? r.querySelector('.crewrivsub').textContent : '',
          lit: head ? head.hasAttribute('data-lit') : null,
          role: head ? head.getAttribute('role') : null,
          fold: !!r.querySelector('.crewwho[data-who]'),
        };
      })""")
    ghost = [r for r in rows if r["name"] == "Ghost Crew"]
    assert ghost, f"Ghost Crew is not in the list at all: {[r['name'] for r in rows]}"
    g = ghost[0]
    assert not g["lit"], "a crew with no ground is marked as having ground to light"
    assert g["role"] != "button", (
        "a crew holding nothing is announced as a button, and pressing it does nothing")
    assert g["name"] and g["sub"], (
        "the row stopped saying anything as well as stopping being a control")
    # The fold stays. Who rode for them is a fair question about a crew with no ground -- the
    # answer is usually nobody, and `crew.rivals.none` says so.
    assert g["fold"], "the Best riders fold was taken off a crew that holds nothing"

    lit = [r for r in rows if r["lit"]]
    assert lit, "no row in the list is pressable, so the rule is firing on everything"


def _shine_count(page):
    """How many squares are in the shine source right now.

    `blink` empties it at 1420ms, so every caller reads it before that.
    """
    return page.evaluate("""() => {
        const s = window.__map.getSource('crew-shine-src');
        if (!s) return null;
        let d = null;
        try { d = s.serialize().data; } catch (e) { d = s._data; }
        return d && d.features ? d.features.length : null;
      }""")


def test_highlight_lights_every_square_a_crew_holds_and_not_only_the_patch_it_frames(rivals):
    """The tester's report, on the real map: "highlight squares is not highlighting all the
    squares".

    This is the NAME in Other crews, which runs the wave. The map popup's own button is the
    other half of the pair and keeps to one patch; `test_the_popup_lights_one_patch_and_walks
    _to_the_next` below holds that end.

    `patchFeatures` built its features from `biggestPatch(pts)` rather than from `pts`, so the
    shine source only ever received one connected component. On production that was 88 of
    Jonah's turf's 108 squares and 30 of Arctic Wheelers' 49 -- and because the camera flies
    to the patch it lights, a tap on a square in the smaller patch sent the map away from the
    square under the finger.

    Rival Crew holds a 3x3 in Copenhagen and a 2x2 in Gothenburg, so 13 held against a biggest
    patch of 9: the number in the source is the whole assertion. `tests/test_crews_highlight_
    scope.py` owns the geometry under node; this is the same question asked of the real
    territory payload, the real flood fill and the real GeoJSON source.
    """
    page = rivals
    _rivals_open(page)
    page.evaluate("() => window.EUCCrews.setOverlay(true)")
    page.wait_for_timeout(300)
    held = page.evaluate("""async () => {
        const r = await fetch('/api/v1/crews?limit=60');
        const b = await r.json();
        const row = document.querySelector('.crewrivrow');
        const slug = row && row.dataset.rival;
        const c = (b.crews || []).find(x => x.slug === slug);
        return c ? { slug: slug, tiles: c.tiles, best: c.best_tiles, regions: c.regions } : null;
      }""")
    assert held, "the first row in Other crews names no crew the API knows"
    assert held["tiles"] > held["best"], (
        f"{held['slug']} holds {held['tiles']} squares in a biggest patch of {held['best']}; "
        "the fixture's outpost did not materialise, so this test cannot tell the two apart")
    page.click(".crewrivrow .crewrivhead")
    # `framePatch` flies for 700ms and the first peak is at 760ms; the source is emptied at
    # 1420ms.
    page.wait_for_timeout(1100)
    lit = _shine_count(page)
    assert lit == held["tiles"], (
        f"{lit} squares were lit of the {held['tiles']} that crew holds; its biggest patch is "
        f"{held['best']}, so this is the patch and not the ground")
    # Back over Browser Crew's ground in Oslo. The camera test below measures a crossing of
    # the Skagerrak, and it starts from wherever this left the map.
    page.evaluate("() => window.__map.jumpTo({ center: [10.7522, 59.9139], zoom: 10 })")
    page.wait_for_timeout(200)


def test_pressing_a_rival_name_flies_to_its_ground_and_lights_it(rivals):
    """The gesture that was never wired here, and the one Erwin asked for by name.

    `shine` frames the crew's biggest patch and sweeps the wave out from the middle of it,
    over every square the crew holds. Erwin asked for this one here specifically -- "can we
    do the other highlight (not the blinking) when the name of the crew is clicked? I prefer
    the wave one for this" -- and the popup kept the two flashes. Rival Crew's ground is in
    Copenhagen and the panel opens framed on Browser Crew's in Oslo, so a camera that has
    crossed the Skagerrak is a camera that went where it was told.

    Last in the file on purpose: on a phone this closes the panel, which is deliberate and is
    the final assertion.
    """
    page = rivals
    _rivals_open(page)
    page.evaluate("() => window.EUCCrews.setOverlay(true)")
    page.wait_for_timeout(300)
    before = page.evaluate("() => { const c = window.__map.getCenter();"
                           "        return [c.lng, c.lat]; }")
    page.click(".crewrivrow .crewrivhead")
    # `framePatch` flies for 700ms; the wave's first peak is at 760ms.
    page.wait_for_timeout(1100)
    after = page.evaluate("() => { const c = window.__map.getCenter();"
                          "        return [c.lng, c.lat]; }")
    shine = page.evaluate("""() => {
        const s = window.__map.getSource('crew-shine-src');
        if (!s) return null;
        let d = null;
        try { d = s.serialize().data; } catch (e) { d = s._data; }
        return d && d.features ? d.features.length : null;
      }""")
    moved = abs(after[0] - before[0]) + abs(after[1] - before[1])
    assert moved > 0.5, (
        f"the camera was at {before} and is at {after}; pressing a crew's name did not take "
        "the map to its ground")
    # Copenhagen, which is where that crew's only patch is.
    assert abs(after[1] - 55.6761) < 1.5 and abs(after[0] - 12.5683) < 2.5, (
        f"the camera ended at {after}, which is not over the patch it was asked to frame")
    assert shine, (
        f"the shine source holds {shine!r} features, so the patch was framed and never lit")
    assert page.evaluate("""() => { const p = document.getElementById('panel');
                                    return !(p && p.classList.contains('open')); }"""), (
        "the panel is still covering the map after Highlight, so the animation it started is "
        "playing behind the control that started it -- the same bug the map popup's own "
        "Highlight button already had to fix")


# Each locale's longest possible standing, read off the shipped tables rather than copied
# into this file, so a retranslation that lengthens an ordinal is measured on the next run.
WORST_ORDINALS = [{'loc': 'da', 'text': '21. ▲12'}, {'loc': 'de', 'text': '21. ▲12'}, {'loc': 'es', 'text': '21.º ▲12'}, {'loc': 'es-419', 'text': '21.º ▲12'}, {'loc': 'fr', 'text': '21e ▲12'}, {'loc': 'it', 'text': '21° ▲12'}, {'loc': 'ja', 'text': '21位 ▲12'}, {'loc': 'ko', 'text': '21위 ▲12'}, {'loc': 'nl', 'text': '21e ▲12'}, {'loc': 'no', 'text': '21. ▲12'}, {'loc': 'pl', 'text': '21. ▲12'}, {'loc': 'pt-BR', 'text': '21º ▲12'}, {'loc': 'ru', 'text': '21-е ▲12'}, {'loc': 'sv', 'text': '21:a ▲12'}, {'loc': 'tr', 'text': '21. ▲12'}, {'loc': 'uk', 'text': '21-е ▲12'}, {'loc': 'zh', 'text': '第21 ▲12'}, {'loc': 'zh-Hant', 'text': '21位 ▲12'}, {'loc': 'en', 'text': '21th ▲12'}]


# ---------------------------------------------------------------------------------------
# The map popup: one patch, the walk, and the name as the way into the crew.
# ---------------------------------------------------------------------------------------

def _rival_slug(page):
    """Rival Crew's slug, which is not the store_id of the rider who founded it.

    `crews.create` slugs the NAME, so `brw-rival` owns `rival-crew` -- and a test that hard
    codes the store_id reads the territory payload, finds no such crew, and says the fixture
    has no ground.
    """
    slug = page.evaluate("""async () => {
        const b = await fetch('/api/v1/crews?limit=60').then(r => r.json());
        const c = (b.crews || []).find(x => x.name === 'Rival Crew');
        return c ? c.slug : null;
      }""")
    assert slug, "the API does not list Rival Crew"
    return slug


def _patches_of(page, slug):
    """A crew's patches, biggest first, read off the real territory payload in the page."""
    return page.evaluate("""async (slug) => {
        const t = await fetch('/api/v1/territory').then(r => r.json());
        const idx = t.crews.findIndex(c => c.slug === slug);
        if (idx < 0) return null;
        const pts = [];
        for (let i = 0; i < t.cells.length; i += 5)
          if (t.cells[i] === idx) pts.push([t.cells[i + 1], t.cells[i + 2]]);
        const own = new Set(pts.map(p => p[0] + ':' + p[1])), seen = new Set(), out = [];
        for (const p of pts) {
          const k0 = p[0] + ':' + p[1];
          if (seen.has(k0)) continue;
          seen.add(k0);
          const st = [p], comp = [];
          while (st.length) {
            const q = st.pop(); comp.push(q);
            for (const [dx, dy] of [[1,0],[-1,0],[0,1],[0,-1]]) {
              const k = (q[0]+dx) + ':' + (q[1]+dy);
              if (own.has(k) && !seen.has(k)) { seen.add(k); st.push([q[0]+dx, q[1]+dy]); }
            }
          }
          out.push(comp);
        }
        out.forEach(c => c.sort((a, b) => a[1] - b[1] || a[0] - b[0]));
        out.sort((a, b) => b.length - a.length || a[0][1] - b[0][1] || a[0][0] - b[0][0]);
        return { z: t.z, patches: out };
      }""", slug)


def _tap_patch(page, slug, which):
    """Tap a square of `slug`'s patch number `which` on the real map.

    The camera is jumped to that square with the panel's own height as bottom padding, so the
    square lands in the strip of map a reader can actually see and the click is a real mouse
    click that MapLibre hit-tests for itself. `page.click` on the canvas would land in the
    middle of it, which is a different square or none.
    """
    # The panel goes first, and the overlay stays. On a 390x844 phone the panel is 76dvh, so
    # the strip of map above it is about 203px tall -- and the topbar is a card occupying the
    # left of it down to y=183. A tap aimed into that strip lands on the chrome over the map,
    # MapLibre never sees it, and the only symptom is a popup that does not appear. With the
    # panel shut the whole viewport is map, which is also how a reader taps a square.
    # `setOverlay(true)` is what keeps the crew layers painted with the panel shut -- the
    # mode's own `hide()` REMOVES them, and a tap with no `crew-fill` to hit is a tap on the
    # basemap. The host's `closePanel` is the same function the panel's own close button runs.
    page.evaluate("() => window.EUCCrews.setOverlay(true)")
    page.evaluate("() => { if (window.closePanel) window.closePanel(); }")
    page.wait_for_timeout(300)
    assert page.evaluate("() => !!window.__map.getLayer('crew-fill')"), (
        "the crew fill is not on the map, so there is no square to tap")
    info = _patches_of(page, slug)
    assert info and len(info["patches"]) > which, (
        f"{slug} has {len(info['patches']) if info else 0} patches; this test needs "
        f"{which + 1}")
    patch = info["patches"][which]
    pt = page.evaluate("""([q, z]) => {
        const lon = x => x / Math.pow(2, z) * 360 - 180;
        const lat = y => { const n = Math.PI - 2 * Math.PI * y / Math.pow(2, z);
          return 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))); };
        const c = { lng: (lon(q[0]) + lon(q[0] + 1)) / 2,
                    lat: (lat(q[1]) + lat(q[1] + 1)) / 2 };
        const panel = document.querySelector('.panel.open');
        const ph = panel ? window.innerHeight - panel.getBoundingClientRect().top : 0;
        window.__map.jumpTo({ center: [c.lng, c.lat], zoom: 13, padding: { bottom: ph } });
        const p = window.__map.project(c);
        const r = window.__map.getCanvasContainer().getBoundingClientRect();
        return [r.left + p.x, r.top + p.y];
      }""", [patch[0], info["z"]])
    page.wait_for_timeout(900)
    # What is actually under that point. `page.mouse.click` dispatches at a coordinate
    # whatever is there, so without this the failure is a missing popup and no reason given.
    on = page.evaluate("""([x, y]) => {
        const el = document.elementFromPoint(x, y);
        return el ? (el.className || el.tagName) + '' : null;
      }""", [pt[0], pt[1]])
    assert on and ("maplibregl-canvas" in on or "mapboxgl-canvas" in on), (
        f"the point meant for a square of {slug} has {on!r} over it, so the tap would land on "
        "the chrome and the map would never see it")
    page.mouse.click(pt[0], pt[1])
    page.wait_for_selector(".crewpop", timeout=10000)
    return info


def _popup(page):
    return page.evaluate(r"""() => {
        const p = document.querySelector('.crewpop');
        if (!p) return null;
        const nm = p.querySelector('[data-det]');
        return {
          text: p.innerText.replace(/\n+/g, ' | '),
          counter: (p.querySelector('.crewpopct') || {}).textContent || null,
          buttons: [...p.querySelectorAll('button')].map(b => b.textContent.trim()),
          name: nm ? { tag: nm.tagName.toLowerCase(), role: nm.getAttribute('role'),
                       label: nm.getAttribute('aria-label'),
                       tab: nm.getAttribute('tabindex') } : null,
        };
      }""")


def _restore_mine(page):
    """Unfold your own crew's card again.

    `mineShut` is module state in crews.js and these tests share one page, so a test that
    folds the card away leaves it folded for every test after it. Opening the `<details>`
    clears the flag through the same `ontoggle` the reader's own hand goes through.
    """
    page.evaluate("""() => { const d = document.querySelector('details.crewmine-wrap');
                             if (d && !d.open) d.open = true; }""")


def test_the_popup_lights_one_patch_and_walks_to_the_next(rivals):
    """Erwin, after the round that made Highlight light everything: "lets convert the
    highlight squares blinking to highlight patch (so it only blinks the patch) and I wonder
    instead of 'details' we can have 'Next patch' as the other option (IF available) and zoom
    and highlight the next patch to browse patches".

    Rival Crew holds 9 squares in Copenhagen and 4 in Gothenburg, 250km apart, which is the
    fixture that can tell the three claims apart:

      * the button lights the patch under the finger (9, or 4) and not the 13 held;
      * Next flies to the other patch, lights THAT one, and brings the popup back on it;
      * the counter names where you are, and it comes from the same flood fill the walk uses.

    None of this is visible to the node tests, which own the geometry: the popup is built by
    maplibre on `addTo`, the facts under the name are read from the tile, and the hop is three
    timers deep.
    """
    page = rivals
    _rivals_open(page)
    rival = _rival_slug(page)
    info = _tap_patch(page, rival, 1)              # the small Gothenburg outpost
    sizes = [len(p) for p in info["patches"]]
    assert len(sizes) == 2 and sizes[0] > sizes[1], (
        f"the fixture's patches are {sizes}; this test needs a big one and a small one")
    pop = _popup(page)
    assert pop["buttons"] == ["Highlight patch", "Next patch"], (
        f"the popup offers {pop['buttons']}, and a crew with two patches should be offered "
        "both the patch under the finger and the walk")
    assert pop["counter"] == f"patch {len(sizes)} of {len(sizes)}", (
        f"the counter says {pop['counter']!r} for a tap on the smallest of {len(sizes)} "
        "patches")
    # The name is the control Details moved on to.
    assert pop["name"] and pop["name"]["role"] == "button" and pop["name"]["tab"] == "0", (
        f"the crew's name in the popup is {pop['name']!r}, which is not reachable")
    assert "Details" in (pop["name"]["label"] or ""), (
        f"the name's label is {pop['name']['label']!r}, which does not say what pressing it "
        "does")

    # Highlight patch: the patch under the finger, and nothing in the other city.
    page.click(".crewpop [data-hl]")
    page.wait_for_timeout(900)
    lit = _shine_count(page)
    assert lit == sizes[1], (
        f"Highlight patch lit {lit} squares; the patch tapped has {sizes[1]} and the crew "
        f"holds {sum(sizes)}")
    assert page.evaluate("() => !document.querySelector('.crewpop')"), (
        "the popup is still parked over the ground it has just lit")
    page.wait_for_timeout(700)

    # Next patch: the other one, lit, with the popup back on it and the counter moved.
    _tap_patch(page, rival, 1)
    before = page.evaluate("() => { const c = window.__map.getCenter(); return [c.lng, c.lat]; }")
    page.click(".crewpop [data-next]")
    page.wait_for_timeout(1000)
    assert page.evaluate("() => !document.querySelector('.crewpop')"), (
        "the popup stayed up while the next patch was lighting, which is the thing it is "
        "dismissed for")
    hop_lit = _shine_count(page)
    assert hop_lit == sizes[0], (
        f"Next patch lit {hop_lit} squares; the patch it walked to has {sizes[0]}")
    page.wait_for_selector(".crewpop", timeout=10000)
    back = _popup(page)
    assert back["counter"] == f"patch 1 of {len(sizes)}", (
        f"after the walk the popup says {back['counter']!r}; from the last patch the next one "
        "is the first")
    after = page.evaluate("() => { const c = window.__map.getCenter(); return [c.lng, c.lat]; }")
    moved = abs(after[0] - before[0]) + abs(after[1] - before[1])
    assert moved > 0.5, (
        f"the camera was at {before} and is at {after}; Next patch lit ground in another city "
        "and never went there")
    page.evaluate("""() => { const p = document.querySelector('.crewpop');
                             if (p) p.remove(); }""")


def test_a_crew_with_one_patch_is_offered_no_walk(rivals):
    """Erwin asked for Next patch "(IF available)", and a crew whose ground is in one piece is
    where it is not: the walk would fly from the patch to itself and light it again. The
    button is simply absent, and Highlight takes the filled styling instead -- the only
    control on a popup should not look like the secondary one.

    Browser Crew holds a single 3x3 in Oslo, which is the shape a version with `hop` hard
    wired to true passes every other test in this file on.
    """
    page = rivals
    _rivals_open(page)
    mine = page.evaluate("""async () => {
        const r = await fetch('/api/v1/crews/me').then(x => x.json());
        return r && r.crew ? r.crew.slug : null;
      }""")
    assert mine, "the signed-in rider is in no crew"
    info = _tap_patch(page, mine, 0)
    assert len(info["patches"]) == 1, (
        f"this crew holds {len(info['patches'])} patches; the test needs the one-patch case")
    pop = _popup(page)
    assert pop["buttons"] == ["Highlight patch"], (
        f"a crew with one patch is offered {pop['buttons']}, and there is nowhere to walk to")
    assert pop["counter"] is None, (
        f"the popup counts {pop['counter']!r} for a crew with a single patch")
    assert page.evaluate("""() => { const b = document.querySelector('.crewpop [data-hl]');
                                    return b && b.className; }""").find("prime") >= 0, (
        "the only control on the popup is styled as the secondary one")
    page.evaluate("() => { const p = document.querySelector('.crewpop'); if (p) p.remove(); }")
    _restore_mine(page)


def test_the_name_in_the_popup_opens_that_crews_own_row(rivals):
    """Details used to scroll to the board row, which Erwin read as "a bit pointless" -- the
    position and the figures, which the row in Other crews has carried since the round that
    put them there. So it lands on that row instead, flashes it, and unfolds its riders.

    The trap here is measuring it: `openCrewDetail` calls `show()`, which re-renders the panel
    and REPLACES `#pbody`. A reference taken before the press points at a detached node whose
    `scrollTop` is 0 and whose rect is stale -- which is exactly how a first run of this read
    as "the panel never scrolled" while the live panel was correctly centred on the row.
    """
    page = rivals
    _rivals_open(page)
    rival = _rival_slug(page)
    _tap_patch(page, rival, 0)
    page.click(".crewpop [data-det]")
    page.wait_for_timeout(2000)
    seen = page.evaluate("""(slug) => {
        const row = document.querySelector('[data-rival="' + slug + '"]');
        const pb = document.getElementById('pbody');          // live, after the re-render
        if (!row || !pb) return null;
        const rb = row.getBoundingClientRect(), b = pb.getBoundingClientRect();
        const fold = row.querySelector('.crewwho[data-who]');
        return {
          popup: !!document.querySelector('.crewpop'),
          open: !!document.querySelector('.panel.open'),
          onScreen: rb.top < b.bottom && rb.bottom > b.top,
          offCentre: Math.abs((rb.top + rb.bottom) / 2 - (b.top + b.bottom) / 2),
          scrolled: Math.round(pb.scrollTop),
          foldOpen: fold ? fold.getAttribute('aria-expanded') : null,
          riders: (row.querySelector('.crewwhopan:not(.crewpatpan)') || {}).textContent || '',
        };
      }""", rival)
    assert seen, "the crew has no row in Other crews to land on"
    assert not seen["popup"], "the popup is still open over the panel it just scrolled"
    assert seen["open"], "Details did not leave the panel open"
    assert seen["onScreen"], (
        f"the row is off screen after Details: panel scrolled to {seen['scrolled']} and the "
        "row is outside it, so the flash fired where nobody could see it")
    assert seen["offCentre"] < 120, (
        f"the row is {seen['offCentre']:.0f}px off the middle of the panel")
    assert seen["foldOpen"] == "true", (
        "the riders fold is still shut, so Details landed on a row that says no more than the "
        "board row it replaced")
    assert "km" in seen["riders"], (
        f"the fold is open and holds {seen['riders'][:60]!r}, with nobody in it")
    # And it is still open after the next render, which is the half this test could not see.
    # Details opens the panel, the panel renders, and the render rebuilds the row -- so
    # whether the fold survived came down to which of the two landed first. It passed here
    # with code that failed on production, where the render had a `/crews/me` to wait for;
    # forcing one turns the race into an answer.
    page.evaluate("() => window.EUCCrews.show()")
    page.wait_for_timeout(1200)
    after = page.evaluate("""(slug) => {
        const row = document.querySelector('[data-rival="' + slug + '"]');
        const fold = row && row.querySelector('.crewwho[data-who]');
        return { expanded: fold ? fold.getAttribute('aria-expanded') : null,
                 riders: row ? (row.querySelector('.crewwhopan:not(.crewpatpan)') || {}).textContent || '' : '' };
      }""", rival)
    assert after["expanded"] == "true" and "km" in after["riders"], (
        f"after a render the fold is {after['expanded']} and holds "
        f"{after['riders'][:40]!r}: the panel rebuilt the row and threw away what Details "
        "had just opened")
    _restore_mine(page)


def test_opening_another_crew_folds_your_own_out_of_the_way(rivals):
    """Erwin: "I am clicking not on my crew the 'my crew options' should be not expanced since
    I want to check the other crews."

    `.crewmine-wrap` is a `<details open>` carrying the roster, the settings and the manage
    block. On a 390px phone that is most of a screen standing between the press and the row it
    is aiming at, and it is open by default for a good reason of its own -- it holds the only
    actionable things in the feature. So it shuts on the way past, and only for somebody
    else's crew: shut for your own, Details would close the card it was asked to open.
    """
    page = rivals
    _rivals_open(page)
    assert page.evaluate("""() => { const d = document.querySelector('details.crewmine-wrap');
                                    return !!(d && d.open); }"""), (
        "your own crew's card is not open to begin with, so this test cannot tell that "
        "anything closed it")
    _tap_patch(page, _rival_slug(page), 0)
    page.click(".crewpop [data-det]")
    page.wait_for_timeout(1800)
    assert page.evaluate("""() => { const d = document.querySelector('details.crewmine-wrap');
                                    return !!d && !d.open; }"""), (
        "your own crew's card is still expanded after opening somebody else's, so the row "
        "Details went to is behind a screenful of your own roster and settings")
    # And your own crew's name does not close the card it opens.
    _open_panel(page)
    page.evaluate("""() => { const d = document.querySelector('details.crewmine-wrap');
                             if (d) d.open = true; }""")
    mine = page.evaluate("""async () => {
        const r = await fetch('/api/v1/crews/me').then(x => x.json());
        return r && r.crew ? r.crew.slug : null;
      }""")
    assert mine, "the signed-in rider is in no crew, so there is nothing to leave open"
    page.evaluate("(slug) => window.EUCCrews.openCrew(slug)", mine)
    page.wait_for_timeout(1500)
    assert page.evaluate("""() => { const d = document.querySelector('details.crewmine-wrap');
                                    return !!(d && d.open); }"""), (
        "opening your OWN crew shut your own crew's card")


def test_the_patches_fold_lists_a_crews_patches_and_lights_the_one_you_press(rivals):
    """The fold Erwin asked for beside Best riders, on the real territory.

    Rival Crew's 4-square outpost is 250km from its 9-square patch, so a row that lights the
    wrong patch sends the camera to another city and a row that lights the crew lights 13.
    """
    page = rivals
    _rivals_open(page)
    page.evaluate("() => window.EUCCrews.setOverlay(true)")
    rival = _rival_slug(page)
    rows = page.evaluate(r"""(slug) => {
        const b = document.querySelector('[data-pats="' + slug + '"]');
        if (!b) return null;
        if (b.getAttribute('aria-expanded') !== 'true') b.click();
        const pan = document.getElementById('crewpat-' + slug);
        return { label: b.textContent.trim(), open: !pan.hidden,
                 rows: [...pan.querySelectorAll('.crewprow')].map(r => ({
                   i: r.getAttribute('data-pati'),
                   text: r.textContent.replace(/\s+/g, ' ').trim() })) };
      }""", rival)
    assert rows, "a crew with two patches has no patches fold"
    assert any(ch.isdigit() for ch in rows["label"]), (
        f"the patches fold is opened from {rows['label']!r} rather than from the patch count "
        "the row above already prints")
    assert len(rows["rows"]) == 2, (
        f"the fold lists {len(rows['rows'])} rows for a crew with two patches: {rows['rows']}")
    assert "9 squares" in rows["rows"][0]["text"] and "4 squares" in rows["rows"][1]["text"], (
        f"the rows read {[r['text'] for r in rows['rows']]}, which is not 9 then 4")
    assert "km" in rows["rows"][0]["text"], "a row carries no distance"
    # And the crew with one patch has no fold at all, which is the dead-control rule.
    assert page.evaluate("""() => {
        const rows = [...document.querySelectorAll('.crewrivrow')];
        const one = rows.find(r => !r.querySelector('[data-pats]'));
        return !!one;
      }"""), "every crew in the list has a patches fold, including single-patch ones"
    page.click(f'#crewpat-{rival} .crewprow[data-pati="1"]')
    page.wait_for_timeout(900)
    lit = _shine_count(page)
    assert lit == 4, (
        f"pressing the outpost's row lit {lit} squares; that patch has 4 and the crew holds 13")
    after = page.evaluate("() => { const c = window.__map.getCenter(); return [c.lng, c.lat]; }")
    assert abs(after[1] - 57.7089) < 1.5 and abs(after[0] - 11.9746) < 2.5, (
        f"the camera ended at {after}, which is not over the Gothenburg outpost the pressed "
        "row names")


def test_a_fold_the_reader_opened_survives_the_next_render(rivals):
    """The knock poll renders every thirty seconds when there is news, and `render()` rebuilds
    the whole panel from `me`. Everything a fold did to the DOM went with it, so a reader
    comparing two crews had both folds shut under them mid-comparison -- which was true of
    Best riders before this round and is the same defect Details tripped over.
    """
    page = rivals
    _rivals_open(page)
    rival = _rival_slug(page)
    opened = page.evaluate("""(slug) => {
        const row = document.querySelector('[data-rival="' + slug + '"]');
        const who = row.querySelector('.crewwho[data-who]');
        const pats = row.querySelector('[data-pats]');
        if (who.getAttribute('aria-expanded') !== 'true') who.click();
        if (pats && pats.getAttribute('aria-expanded') !== 'true') pats.click();
        return { who: who.getAttribute('aria-expanded'),
                 pats: pats ? pats.getAttribute('aria-expanded') : null };
      }""", rival)
    assert opened["who"] == "true" and opened["pats"] == "true", (
        f"the folds did not open to begin with: {opened}")
    page.wait_for_timeout(600)
    page.evaluate("() => window.EUCCrews.show()")
    page.wait_for_timeout(1200)
    after = page.evaluate("""(slug) => {
        const row = document.querySelector('[data-rival="' + slug + '"]');
        const who = row.querySelector('.crewwho[data-who]');
        const pats = row.querySelector('[data-pats]');
        const pan = row.querySelector('.crewwhopan:not(.crewpatpan)');
        return { who: who.getAttribute('aria-expanded'),
                 pats: pats ? pats.getAttribute('aria-expanded') : null,
                 hidden: pan ? pan.hidden : null,
                 riders: pan ? (pan.textContent || '') : '' };
      }""", rival)
    assert after["who"] == "true" and not after["hidden"], (
        f"Best riders came back {after['who']} after a render, with the panel hidden="
        f"{after['hidden']}")
    assert "km" in after["riders"], (
        f"the fold is open and empty after the render: {after['riders'][:40]!r}. The roster is "
        "cached per crew, so re-applying it should cost no request and lose no names")
    assert after["pats"] == "true", (
        "the patches fold came back shut after a render")
    # Leave them as they were found.
    page.evaluate("""(slug) => {
        const row = document.querySelector('[data-rival="' + slug + '"]');
        const who = row.querySelector('.crewwho[data-who]');
        const pats = row.querySelector('[data-pats]');
        if (who.getAttribute('aria-expanded') === 'true') who.click();
        if (pats && pats.getAttribute('aria-expanded') === 'true') pats.click();
      }""", rival)


def test_a_name_in_best_riders_flies_to_where_that_rider_rides(rivals):
    """The names were inert, and the note in crews.js said they had to be: "`flyToRider`,
    which needs a position this payload does not carry". Erwin: "we already allow this in
    Riders". The host's own handler draws the ring, jitters the point by up to 1.7km so it is
    not a doorstep, and closes the panel -- all of which stays in that one function, reached
    through `H`.
    """
    page = rivals
    _open_panel(page)
    page.wait_for_selector(".crewrivrow", timeout=20000)
    page.evaluate("""() => {
        const b = document.querySelector('.crewrivrow .crewwho[data-who]');
        if (b && b.getAttribute('aria-expanded') !== 'true') b.click();
      }""")
    page.wait_for_selector(".crewcfly", timeout=15000)
    seen = page.evaluate("""() => {
        const el = document.querySelector('.crewcfly');
        return { label: el.getAttribute('aria-label'), role: el.getAttribute('role'),
                 lat: +el.getAttribute('data-lat'), lon: +el.getAttribute('data-lon'),
                 names: document.querySelectorAll('.crewcname').length,
                 pressable: document.querySelectorAll('.crewcfly').length };
      }""")
    assert seen["role"] == "button" and seen["label"], (
        f"a rider's name is {seen!r}, which is not a control")
    page.click(".crewcfly")
    page.wait_for_timeout(3200)
    end = page.evaluate("""() => { const c = window.__map.getCenter();
        return { lng: c.lng, lat: c.lat, z: window.__map.getZoom(),
                 ring: !!window.__map.getLayer('area-line'),
                 open: !!document.querySelector('.panel.open') }; }""")
    off = (abs(end["lat"] - seen["lat"]) * 111.32
           + abs(end["lng"] - seen["lon"]) * 111.32)
    assert end["ring"], "no ring was drawn, so the rider's area was never marked"
    assert not end["open"], "the panel is still covering the map it just flew"
    assert off < 6, (
        f"the camera landed {off:.1f}km from the rider it was sent to ({seen['lat']}, "
        f"{seen['lon']}), which is further than the privacy jitter can account for")

