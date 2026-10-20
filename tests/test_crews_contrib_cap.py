"""`contributorsHTML`'s cap: show five, count all of them.

The Best riders list was written for one place -- your own crew's card, where every rider who
rode is printed -- and it now also fills the fold under a row in both browse lists, where a
crew with twenty contributors cannot have twenty lines. So it takes a `limit`.

The trap is the percentage. The function's own note says there is ONE denominator on purpose:
an earlier version measured the bar against the leader and the text against the crew, so the
top rider drew a full-width bar reading "34%" and the rider 3 km behind them drew 97% of that
bar, also reading "34%". Slicing the rows before that arithmetic reintroduces the same class of
lie from the other end -- a crew's top five would each be shown as a share of the top five, so
a two-rider fold would read 61/39 while the same crew's own card read 61/39 of a different
total. The shares have to be computed over everybody and only the ROWS withheld.

Nothing static can see that: the percentages are built by a largest-remainder pass over a
closure variable and rendered into a style attribute forty lines later. So this lifts the real
function out of crews.js and runs it under node, the way `test_crews_join_markup.py` lifts
`joinHTML` and `test_territory_rank_movement.py` lifts `moved`.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

START = "  function contributorsHTML(rows, limit) {"
END = "  function bindContribMore(root) {"

HARNESS = """
// --- stubs. Only what the function touches; none of it decides a share. -------------------
const t = (k, v) => (k === "crew.mine.who" ? "Best riders"
  : k === "crew.you" ? "You"
  : k === "crew.who.share" ? v.n + "%%"
  : k + (v ? JSON.stringify(v) : ""));
const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fmtKm = v => v + " km";
const roleMark = r => (r === "leader" ? '<i class="crewrole">*</i>' : "");
const roleKey = rows => "";
// The overflow label, counting what the fold is hiding -- the same helper and the same
// sentence the roles list uses.
const moreLabel = n => "Show " + n + " more";
const H = {};
const ME = null;

%(region)s

const rider = (name, km) => ({ id: name, name: name, km: km, rides: 2, role: "member",
                               has_avatar: false, flag: null });

// Eight riders whose kilometres are deliberately awkward: the floors sum to 97, so three
// points have to be handed back by the largest-remainder pass.
const EIGHT = [
  rider("a", 100.0), rider("b", 50.0), rider("c", 33.0), rider("d", 25.0),
  rider("e", 20.0),  rider("f", 16.0), rider("g", 14.0), rider("h", 12.0),
];

function shares(html) {
  return [...html.matchAll(/<i>(\\d+)%%<\\/i>/g)].map(m => +m[1]);
}

function rows(html) {
  return [...html.matchAll(/<div class="crewcrow([^"]*)"([^>]*)>/g)]
    .map(m => ({ cls: m[1].trim(), hidden: / hidden/.test(m[2]) }));
}

const out = {};
const all = contributorsHTML(EIGHT);
const five = contributorsHTML(EIGHT, 5);
out.all = { shares: shares(all), rows: rows(all),
            more: (all.match(/crewcmore/g) || []).length };
out.five = { shares: shares(five), rows: rows(five),
             more: (five.match(/crewcmore/g) || []).length,
             label: (/crewcmore[^>]*>([^<]*)</.exec(five) || [])[1] || "" };
// A crew with fewer riders than the cap must not grow a button.
out.two = (() => {
  const h = contributorsHTML([rider("a", 61.0), rider("b", 39.0)], 5);
  return { shares: shares(h), rows: rows(h),
           more: (h.match(/crewcmore/g) || []).length };
})();
// And the cap is not a filter: asking for exactly as many as there are is not an overflow.
out.exact = (() => {
  const h = contributorsHTML(EIGHT.slice(0, 5), 5);
  return { rows: rows(h), more: (h.match(/crewcmore/g) || []).length };
})();
out.empty = contributorsHTML([], 5);
console.log(JSON.stringify(out));
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node to run the shipped js")


def _region():
    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(START)
    j = src.index(END, i)
    region = src[i:j]
    for needed in ("crewcrow", "crewckm", "largest remainder", "var cap ="):
        assert needed.lower() in region.lower(), (
            f"{needed!r} is not in the lifted region; this tests nothing")
    return region


def _run():
    d = pathlib.Path(tempfile.mkdtemp(prefix="contribcap-"))
    try:
        f = d / "t.mjs"
        f.write_text(HARNESS % {"region": _region()}, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_capped_list_reports_the_same_shares_as_an_uncapped_one():
    """The whole reason the cap is a render concern and not a slice.

    Same eight riders, same crew, same percentages. If the cap were applied before the
    arithmetic the five shown would be renormalised to add up to 100 between them, and the
    top rider's 37% would become 46% in a fold while their own crew's card said 37%.
    """
    out = _run()
    assert out["all"]["shares"], "no share was rendered, so this is not asking anything"
    assert out["five"]["shares"][:5] == out["all"]["shares"][:5], (
        f"capped at five the shares read {out['five']['shares'][:5]} and uncapped they read "
        f"{out['all']['shares'][:5]}; the cap is being applied before the denominator")
    assert sum(out["all"]["shares"]) == 100, (
        f"the shares add up to {sum(out['all']['shares'])}, and this card asks the reader to "
        "add them up")
    # The hidden rows keep their real shares too, so unfolding cannot change a number that
    # was already on screen.
    assert out["five"]["shares"] == out["all"]["shares"], (
        "the rows behind the fold were renormalised, so pressing Show more rewrites the "
        "percentages the reader was already looking at")


def test_the_cap_hides_the_tail_rather_than_dropping_it():
    """`hidden`, so the overflow button costs no request -- the same bargain the join list
    makes with its own six. Dropped rows would need a fetch to get back."""
    out = _run()
    shown = [r for r in out["five"]["rows"] if not r["hidden"]]
    hid = [r for r in out["five"]["rows"] if r["hidden"]]
    assert len(out["five"]["rows"]) == 8, (
        f"the fold rendered {len(out['five']['rows'])} rows of eight riders; the tail was "
        "dropped instead of folded")
    assert len(shown) == 5, f"{len(shown)} rows are visible under a cap of five"
    assert len(hid) == 3, f"{len(hid)} rows are folded away"
    for r in hid:
        assert "crewcrest" in r["cls"], (
            f"a folded row carries {r['cls']!r} and nothing can unfold it")


def test_the_overflow_button_counts_what_it_is_hiding():
    """"Show 3 more" over five visible rows, not "Show 8". `crew.roles.more` is the string the
    roles list already uses for exactly this, and its own note says it counts what the fold is
    hiding because "Show all 7" above six visible rows names six rows as missing to reveal
    one."""
    out = _run()
    assert out["five"]["more"] == 1, (
        f"{out['five']['more']} overflow buttons under one capped list")
    assert "3" in out["five"]["label"], (
        f"the overflow button reads {out['five']['label']!r}; five of eight are shown, so it "
        "is hiding three")


def test_nothing_is_folded_when_there_is_nothing_to_fold():
    """A control whose press reveals nothing is the dead-button mistake this file records
    itself making twice."""
    out = _run()
    assert out["all"]["more"] == 0, "an uncapped list grew an overflow button"
    assert out["two"]["more"] == 0, "two riders under a cap of five grew an overflow button"
    assert out["exact"]["more"] == 0, (
        "five riders under a cap of five grew an overflow button, so the cap is firing on "
        "equality")
    assert all(not r["hidden"] for r in out["two"]["rows"]), (
        "a list shorter than the cap folded rows away")
    assert all(not r["hidden"] for r in out["exact"]["rows"]), (
        "a list exactly as long as the cap folded a row away")


def test_a_crew_with_no_riders_renders_nothing_at_all():
    """Not an empty card with a heading. The fold says `crew.rivals.none` in that case, which
    is a sentence about the crew rather than a list with nothing in it."""
    assert _run()["empty"] == "", "an empty contributors list rendered a heading"


def test_the_crews_own_card_still_asks_for_every_rider():
    """The call that was there first. `limit` is optional precisely so this did not change,
    and a default that capped it would silently shorten the crew card's own list."""
    src = CREWS_JS.read_text(encoding="utf-8")
    calls = re.findall(r"contributorsHTML\(([^)]*)\)", src)
    calls = [c for c in calls if "function" not in c]
    assert calls, "contributorsHTML is never called"
    own = [c for c in calls if "r.body.contributors" in c]
    assert own, f"the crew card no longer builds its own contributors list: {calls}"
    for c in own:
        assert "," not in c, (
            f"the crew's own card now passes a limit ({c!r}), so a leader reading who carries "
            "their crew is shown a shortened list")
