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


def _give_ground(db, store_id, clan_id, lat=59.9139, lon=10.7522, zoom=14):
    """Ride a 3x3 block of squares for one crew, so the map has an emblem to paint.

    This is what the file used to lack. Three tests here had to build a bare element and ask
    the stylesheets a question, because no crew in this world held any ground and no `.crewemb`
    was ever created -- which left the one thing worth checking, whether a badge is painted
    where the map projected it, outside the suite's reach.
    """
    from datetime import timedelta

    from models import Trip, TripTrack, utcnow
    from services import tiles as T
    from services import territory

    home = T.parse(T.tile_of(lat, lon, zoom))
    pts = _serpentine(home[1], home[2], zoom)
    when = utcnow() - timedelta(days=2)
    uuid = f"brw-ground-{clan_id}"
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
    page.wait_for_selector(".crewcard", timeout=20000)


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
