"""The row helpers in public.py, executed — because nothing in this suite executed them.

787 tests and a cascade checker passed while the crew card was 95% missing. Three reviewers
opened the panel and found one line in the console:

    ReferenceError: esc is not defined
        at Object.av (public.py's inline script)
        at crews.js -> contributorsHTML

`av()` is the shared avatar helper in the page's own script. I added an initial to the empty
avatar disc and wrote `esc(...)` around it — and `esc` is defined inside the crews.js IIFE, a
different scope, where the page cannot see it. Every contributor in the seed data has
`has_avatar: false`, so the branch always ran and always threw; `contributorsHTML` is the last
term of the single `el.innerHTML = …` assignment that builds the territory block, so the throw
aborted the assignment and the squares, the area, WHERE TO RIDE NEXT, WHAT YOU COULD LOSE and
the contributor list all vanished together. A brand-new crew with no contributors rendered
perfectly, which is exactly why I did not see it.

Every guard in this repo reads source text or speaks HTTP. Not one of them ran the page, so
none of them could have caught an identifier that does not exist. A reviewer named the class:
"an undefined identifier inside a template literal silently deletes a whole UI section."

The honest version of that guard is a browser opening the panel in each signed-in state. There
is no browser in this repo's dev environment and adding one is a dependency decision that is
not mine to make. What IS available is node — `test_crews_plural.py` already lifts shipped JS
and runs it — so this does the same for the helpers that broke: it lifts them out of public.py,
stubs the handful of things they legitimately reach for, and CALLS them, with the empty-avatar
case first.

What this catches: a free identifier, a typo, a signature change, a throw on any of these
paths. What it does not: anything outside the lifted region, and anything that needs layout.
The region is small and the helpers are pure, which is what makes lifting them honest.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "web" / "public.py"

# The lifted region: the row helpers, from the flag through `rider`. Anchored on the first and
# last declaration rather than on line numbers, so moving the block does not silently empty
# this test — if either anchor goes, the test fails loudly instead of testing nothing.
FIRST = "const cc=c=>c?"
LAST_RE = re.compile(r"^const rider=.*$", re.M)

# What the helpers legitimately reach outside themselves. Anything NOT in here and not declared
# in the region is the bug this file exists for.
STUBS = """
globalThis.ADMIN = false;
globalThis.ASPUBLIC = false;
globalThis.API = "/api/v1";
function isAdminView(){ return ADMIN && !ASPUBLIC; }
function t(k){ return "[" + k + "]"; }
"""

CASES = [
    ("a rider with no avatar -- the case that threw",
     {"id": "r1", "has_avatar": False, "name": "Nordlys", "flag": "no"}),
    ("a rider with an avatar",
     {"id": "r2", "has_avatar": True, "name": "Tarmac", "flag": "se"}),
    ("has_avatar absent altogether",
     {"id": "r3", "name": "Fjord", "flag": "dk"}),
    ("no name at all",
     {"id": "r4", "has_avatar": False, "flag": "no"}),
    ("a name that is only spaces",
     {"id": "r5", "has_avatar": False, "name": "   ", "flag": "no"}),
    ("a name opening with a bracket",
     {"id": "r6", "has_avatar": False, "name": "<script>x</script>", "flag": "no"}),
    ("a name opening with a quote",
     {"id": "r7", "has_avatar": False, "name": '"quoted"', "flag": "no"}),
    ("an ampersand",
     {"id": "r8", "has_avatar": False, "name": "&amp; co", "flag": "no"}),
    ("a CJK name, so the initial is not Latin",
     {"id": "r9", "has_avatar": False, "name": "東京", "flag": "jp"}),
    ("an anonymous rider, which takes the dazzle branch",
     {"id": "r10", "anon": True, "alias": "Someone", "mark": "a3f9", "has_avatar": False}),
    ("an anonymous rider with no mark",
     {"id": "r11", "anon": True, "alias": "Nobody", "has_avatar": False}),
    ("a rider with no flag",
     {"id": "r12", "has_avatar": False, "name": "Flagless"}),
]

HARNESS = """
%(stubs)s
%(region)s

const out = [];
for (const [label, e] of %(cases)s) {
  const row = {};
  for (const [name, call] of [
    ["av", () => av(e.id, e.has_avatar, e)],
    ["rider", () => rider(e)],
    ["cc", () => cc(e.flag)],
    ["GLOBE", () => GLOBE()],
    ["anonName", () => anonName(e)],
    ["avInitial", () => avInitial(e)],
  ]) {
    try {
      const v = call();
      row[name] = typeof v === "string" ? v.length : typeof v;
    } catch (err) {
      row[name] = "THREW: " + (err && err.message ? err.message : String(err));
    }
  }
  row.initial = (() => { try { return avInitial(e); } catch (err) { return "THREW"; } })();
  out.push([label, row]);
}
console.log(JSON.stringify(out));
"""


def _region():
    src = PUBLIC.read_text(encoding="utf-8")
    start = src.index(FIRST)
    m = LAST_RE.search(src, start)
    assert m, "`const rider=` is gone from public.py; this test is aimed at nothing"
    return src[start:m.end()]


def _run():
    region = _region()
    # The region must actually contain the helpers, or a passing run proves nothing.
    for needed in ("const av=", "const rider=", "function dazzle", "const avInitial="):
        assert needed in region, f"{needed} is not in the lifted region"
    js = HARNESS % {"stubs": STUBS, "region": region,
                    "cases": json.dumps(CASES, ensure_ascii=False)}
    d = pathlib.Path(tempfile.mkdtemp(prefix="pubrender-"))
    try:
        f = d / "t.mjs"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (
            "the lifted helpers would not even load:\n" + (r.stdout or "") + (r.stderr or ""))
        return dict(json.loads(r.stdout))
    finally:
        shutil.rmtree(d, ignore_errors=True)


pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node to run the shipped js")


def test_no_row_helper_throws_on_any_shape_of_rider():
    """The one that would have gone red. `esc is not defined` is a THREW on every row."""
    rows = _run()
    broke = []
    for label, row in rows.items():
        for name, v in row.items():
            if isinstance(v, str) and v.startswith("THREW"):
                broke.append(f"  {name}()  {label}\n      {v}")
    assert not broke, (
        f"{len(broke)} of public.py's row helpers throw. A throw here is not one broken row: "
        f"`contributorsHTML` is the last term of the single assignment that builds the whole "
        f"territory block, so the squares, the area and both target lists go with it.\n"
        + "\n".join(broke))


def test_every_helper_returns_something_to_render():
    """A helper that returns undefined paints the string "undefined" into the panel."""
    rows = _run()
    empty = [f"  {name}()  {label}" for label, row in rows.items()
             for name, v in row.items()
             if name != "initial" and not isinstance(v, int)]
    assert not empty, "these returned no string at all:\n" + "\n".join(empty)


def test_the_initial_is_one_safe_character():
    """It goes into HTML by interpolation, so it carries the whole escaping question itself.

    A whitelist rather than an escaper, because it is one character: `esc` was not in scope
    and for a single glyph nothing that matters in HTML is a letter anyway.
    """
    rows = _run()
    bad = []
    for label, row in rows.items():
        c = row["initial"]
        if not isinstance(c, str) or len(c) != 1:
            bad.append(f"  {label}: {c!r} is not a single character")
        elif c in '<>&"\'`\\/':
            bad.append(f"  {label}: {c!r} would need escaping and is not escaped")
    assert not bad, "the avatar initial is interpolated raw into HTML:\n" + "\n".join(bad)


def test_an_anonymous_rider_never_leaks_the_real_name():
    """`anonView` exists so an alias is shown instead of a name; the initial must agree."""
    rows = _run()
    anon = rows["an anonymous rider, which takes the dazzle branch"]
    assert anon["initial"] == "S", (
        f"the initial came from the wrong field: {anon['initial']!r}, expected the alias's S")
