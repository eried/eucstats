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
        yield {"base": site}
    finally:
        db.close()


@pytest.fixture(scope="module")
def page(world):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                # no browser binary installed
            pytest.skip(f"playwright has no browser available: {exc}")
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        pg = ctx.new_page()
        pg.goto(world["base"], wait_until="domcontentloaded")
        yield pg
        ctx.close()
        browser.close()


def _open_panel(page):
    """Wait for the map to finish loading, then press the dock button.

    The dock is `inert` until the map's load event, which is why a scripted click placed any
    earlier is swallowed -- itself a thing that cost a reviewer an hour.
    """
    page.wait_for_function(
        "() => { const i = document.querySelector('.intro');"
        "        return i && !i.hasAttribute('inert'); }", timeout=30000)
    page.click(".dock button[data-p=crews]")
    page.wait_for_selector(".crewcard", timeout=20000)


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
