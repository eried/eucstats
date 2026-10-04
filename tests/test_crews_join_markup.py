"""The join list's markup, balanced, for every state a row can be in.

Round 20 found that `joinHTML`'s `full` branch ended `"</button></div>"` while the row
expression appended `+ "</div>"` regardless: one `<div class="crewrow">` opened and two
closed. `full` is true for every row while the rider is inside the seven-day cooling-off
window, so this fired for anybody who had just left a crew -- which every rider does
eventually, and which no round before 20 had walked.

The surplus closer does not look like much in the source. What it did on screen: row 1's extra
`</div>` closed `#cj-list`, row 2's closed `.crewjoin`, and rows 3 through 21 ended up loose in
the panel at a different width with the card's border stopping mid-list. Everything scoped to
the list then governed one row -- the counter read "1 of 1 crews" above twenty-one rows, the
filter and the "Show all 21" toggle acted on one of them, and typing nonsense printed "Nothing
matches" with twenty crews still visible underneath it.

It is also latent in the ordinary browse list: `full` is `(MAXMEM && c.members >= MAXMEM) ||
!!waiting`, and `max_members` is 0 today, so the moment an admin configures a member cap the
identical break starts at the first full crew.

No static check can see this. A tag counter over the source counts the `</div>` in the branch
and the one at the end and has no idea only one of them runs; the strings are concatenated
across forty lines of comments. So this lifts the real `joinHTML` out of crews.js and runs it
under node against stub helpers, the way `test_crews_plural.py` lifts the plural machinery and
`test_crews_write_dedupe.py` lifts `api()`, and counts tags in the HTML that comes out.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

# Anchored on text, not line numbers: moving the function fails loudly rather than testing
# nothing. `bindList` is the next declaration after it.
START = "  function joinHTML(crews, me) {"
END = "  function bindList() {"

HARNESS = """
// --- stubs. Only the shapes `joinHTML` touches; nothing here decides the markup. ----------
const t = (k, v) => k + (v ? "(" + JSON.stringify(v) + ")" : "");
const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const emb = (slug, px) => '<i class="crewemb" data-s="' + esc(slug) + '"></i>';
const riders = n => n + " riders";
const fmtKm2 = v => v + " km2";
const fmtKm = v => v + " km";
const days = n => n + " days";
const daysUntil = () => 7;
// `groundAway` returns null with no map loaded, which is the real behaviour on a cold panel.
const groundAway = () => null;
let MAXMEM = 0;

%(region)s

// --- a tag counter. Only the elements this markup nests; `<input>`, `<i>` and `<br>` are
// void or self-closed and would skew a naive count. -----------------------------------------
function balance(html) {
  const open = {}, close = {};
  for (const m of html.matchAll(/<(\\/?)(div|button|span|h3|b)\\b[^>]*>/g)) {
    const t = m[2];
    (m[1] ? close : open)[t] = ((m[1] ? close : open)[t] || 0) + 1;
  }
  const out = {};
  for (const k of new Set([...Object.keys(open), ...Object.keys(close)])) {
    out[k] = [open[k] || 0, close[k] || 0];
  }
  return out;
}

// Does every tag close in the right order, and does nothing close a parent early?
function wellFormed(html) {
  const stack = [];
  for (const m of html.matchAll(/<(\\/?)(div|button|span|h3|b)\\b[^>]*>/g)) {
    if (m[1]) {
      if (stack.pop() !== m[2]) return false;
    } else {
      stack.push(m[2]);
    }
  }
  return stack.length === 0;
}

// How deep the LAST row sits relative to the first. If a row leaks a closer, every row after
// it sits one level shallower -- which is precisely what put rows 3+ on the panel background.
function rowDepths(html) {
  const depths = [];
  let depth = 0;
  for (const m of html.matchAll(/<(\\/?)(div|button|span|h3|b)\\b([^>]*)>/g)) {
    if (m[1]) { depth -= 1; continue; }
    // `crewrow` followed by a space or the closing quote. Without the lookahead this also
    // matched the row's own inner `<div class="crewrown">` -- `crewrow` is a prefix of
    // `crewrown` -- and recorded every row twice, at two different depths. Same shape as the
    // `.crewmore` / `.crewmorerow` collision an earlier round spent a commit on.
    if (m[2] === "div" && /class="crewrow(?=[ "])/.test(m[3])) depths.push(depth);
    depth += 1;
  }
  return depths;
}

const crew = (i, extra) => Object.assign({
  slug: "crew-" + i, name: "Crew " + i, members: 4, join_policy: "open", km2: 12,
}, extra || {});

function run() {
  const out = {};
  const many = [];
  for (let i = 1; i <= 21; i += 1) many.push(crew(i));

  // 1. The ordinary list: no cooldown, no member cap.
  let h = joinHTML(many.map(c => Object.assign({}, c)), {});
  out.plain = { tags: balance(h), wellFormed: wellFormed(h), depths: rowDepths(h) };

  // 2. THE REGRESSION. Cooling off, so `full` is true for every row.
  h = joinHTML(many.map(c => Object.assign({}, c)),
               { cooldown_until: "2026-10-11T00:00:00Z" });
  out.cooling = { tags: balance(h), wellFormed: wellFormed(h), depths: rowDepths(h),
                  rows: (h.match(/class="crewrow(?=[ "])/g) || []).length };

  // 3. A member cap, which is the same branch reached the other way -- and the way it will
  //    be reached in the browse list the day an admin sets `max_members`.
  MAXMEM = 4;
  h = joinHTML(many.map(c => Object.assign({}, c)), {});
  out.capped = { tags: balance(h), wellFormed: wellFormed(h), depths: rowDepths(h) };
  MAXMEM = 0;

  // 3b. WAITING on a leader. `join()` refuses while the request stands, so every row is
  //     disabled -- but it keeps its own label, because "Cooling off" would be false and the
  //     reason belongs above the list once rather than in all twenty-one rows.
  h = joinHTML(many.map(c => Object.assign({}, c)), { status: "pending" });
  out.waiting = { tags: balance(h), wellFormed: wellFormed(h), depths: rowDepths(h),
                  rows: (h.match(/class="crewrow(?=[ "])/g) || []).length,
                  disabled: (h.match(/disabled/g) || []).length,
                  saysCoolingOff: h.indexOf("crew.join.wait.btn") >= 0,
                  heading: /<h3>([^<]*)</.exec(h)[1],
                  message: h.indexOf("crew.join.pending") >= 0 };

  // 4. Mixed: some crews full, some not, so one branch cannot mask the other.
  h = joinHTML(many.map((c, i) => Object.assign({}, c, { members: i %% 2 ? 9 : 2 })), {});
  MAXMEM = 0;
  out.mixedPolicies = { wellFormed: wellFormed(h) };

  // 5. The three join policies and a description, all in one list.
  h = joinHTML([
    crew(1, { join_policy: "open" }),
    crew(2, { join_policy: "invite" }),
    crew(3, { join_policy: "approval", description: "we ride the ring road" }),
  ], {});
  out.policies = { tags: balance(h), wellFormed: wellFormed(h), depths: rowDepths(h) };

  // 6. Fewer crews than the toggle threshold: no filter, no counter.
  h = joinHTML([crew(1), crew(2)], {});
  out.few = { wellFormed: wellFormed(h), hasFilter: h.indexOf("cj-filter") >= 0 };

  console.log(JSON.stringify(out));
}

run();
"""


def _region():
    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(START)
    j = src.index(END, i)
    region = src[i:j]
    for needed in ('class="crewrow', "var locked =", "var pending =", 'id="cj-list"',
                   "</button>"):
        assert needed in region, f"{needed} is not in the lifted region; this tests nothing"
    return region


pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node to run the shipped js")


def _run():
    d = pathlib.Path(tempfile.mkdtemp(prefix="joinmarkup-"))
    try:
        f = d / "t.mjs"
        f.write_text(HARNESS % {"region": _region()}, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_cooling_off_rider_gets_a_list_that_closes_itself():
    """The regression, in the state that triggered it: every row's button is disabled."""
    out = _run()["cooling"]
    opened, closed = out["tags"]["div"]
    assert opened == closed, (
        f"{opened} <div> opened and {closed} closed while cooling off. Each surplus closer "
        "shuts a real ancestor: the first ends #cj-list, the second ends .crewjoin, and every "
        "row after that renders loose on the panel background -- so the counter, the filter "
        "and the show-all toggle all end up governing one row out of twenty-one.")
    assert out["wellFormed"], "the markup does not nest correctly"


def test_every_row_in_a_cooling_off_list_sits_at_the_same_depth():
    """The visible consequence, stated directly: row 3 must not be shallower than row 1."""
    out = _run()["cooling"]
    assert out["rows"] == 21, f"expected 21 rows, got {out['rows']}"
    depths = out["depths"]
    assert len(set(depths)) == 1, (
        "the rows are at different nesting depths: "
        f"{sorted(set(depths))}. A row that leaks a closer drops every row after it one level "
        f"out of the list. First six depths: {depths[:6]}")


def test_a_member_cap_does_not_break_the_browse_list():
    """`max_members` is 0 today. The same branch, reached the way an admin will reach it."""
    out = _run()["capped"]
    opened, closed = out["tags"]["div"]
    assert opened == closed, (
        f"with a member cap configured, {opened} <div> opened and {closed} closed -- the "
        "cooling-off break, latent in the ordinary browse list until an admin sets a cap")
    assert out["wellFormed"]
    assert len(set(out["depths"])) == 1


def test_a_waiting_rider_gets_a_browsable_list_that_closes_itself():
    """The state that used to be a dead end. Structure first: it is the same row builder, so
    the closer bug would reappear here if it reappeared anywhere."""
    out = _run()["waiting"]
    opened, closed = out["tags"]["div"]
    assert opened == closed, f"{opened} <div> opened, {closed} closed while waiting"
    assert out["wellFormed"]
    assert len(set(out["depths"])) == 1
    assert out["rows"] == 21, f"expected 21 browsable rows, got {out['rows']}"


def test_a_waiting_rider_sees_every_row_disabled_but_not_called_cooling_off():
    """`join()` refuses while the request stands, so an enabled button would be a lie -- and
    "Cooling off" is a different situation with a seven-day clock attached."""
    out = _run()["waiting"]
    assert out["disabled"] >= 21, (
        f"only {out['disabled']} disabled attributes for 21 rows: a waiting rider would get "
        "buttons that fail with 'Leave your crew first.'")
    assert not out["saysCoolingOff"], (
        "the rows say 'Cooling off', which is a different state with a clock on it")
    assert out["heading"] == "crew.join.h", (
        f"heading is {out['heading']!r}; a waiting rider is not cooling off")
    assert out["message"], "nothing on the card says why every row is disabled"


def test_a_cooling_off_rider_is_still_told_it_is_a_cooldown():
    """The mirror: separating the two reasons must not blur the one that already worked."""
    out = _run()["cooling"]
    assert out["tags"]["div"][0] == out["tags"]["div"][1]


def test_a_mixed_list_is_balanced():
    """Some rows full and some not, so neither branch can hide behind the other."""
    assert _run()["mixedPolicies"]["wellFormed"]


def test_the_ordinary_list_is_balanced():
    """The state every round before 20 looked at, kept as the control."""
    out = _run()["plain"]
    for tag, (opened, closed) in out["tags"].items():
        assert opened == closed, f"<{tag}>: {opened} opened, {closed} closed"
    assert out["wellFormed"]
    assert len(set(out["depths"])) == 1


def test_all_three_join_policies_and_a_description_are_balanced():
    out = _run()["policies"]
    for tag, (opened, closed) in out["tags"].items():
        assert opened == closed, f"<{tag}>: {opened} opened, {closed} closed"
    assert out["wellFormed"]


def test_a_short_list_has_no_filter_and_still_closes():
    """Below the toggle threshold the filter and counter are not rendered at all."""
    out = _run()["few"]
    assert out["wellFormed"]
    assert not out["hasFilter"], "two crews should not get a search box"
