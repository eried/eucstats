"""Nineteen languages do not count the same way, and two of them were counting like English.

`plural()` chooses between three wordings by arithmetic, and one arithmetic rule was applied to
every language in the set. That is safe for the middle wording, and only because the data
agrees: a locale with no "few" category has `.few` word-for-word identical to its plural, so
the 2-3-4 branch decides nothing for it. The first test below is what makes that true rather
than lucky.

It is not safe for the singular. Russian and Ukrainian take the singular back at 21, 31, 101
and 121; Polish, which looks like the same rule, does not -- `21 pól` is correct there. The
board prints crew sizes in the tens and square counts in the hundreds, so `n % 10 == 1` is one
row in ten, and every one of them read `21 клеток`: genitive plural, where the language wants
`21 клетка`.

The second test runs the shipped `plural()` under node against the real translation tables and
checks what it returns against the CLDR category for that language and that number.

The third says out loud what is still missing. Two families pass no singular wording at all,
which is correct for 1 -- both of their callers guard with `> 1` -- and wrong at 21 in Russian
and Ukrainian, where there is nothing to give them. Closing it needs two new keys through the
translation workflow rather than nineteen hand-written noun forms, so it is marked as broken
here instead of being quietly blessed by a test that expects the wrong answer.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

NL = chr(10)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
from i18n import EN                            # noqa: E402
from i18n_data import TRANSLATIONS             # noqa: E402

CREWS_JS = ROOT / "web" / "static" / "crews.js"

# one, few, many -- as `plural()` is called. `None` where the code passes no singular.
WITH_ONE = [
    ("crew.tile1", "crew.tiles.few", "crew.tiles"),
    ("crew.rider1", "crew.riders.few", "crew.riders"),
    ("crew.day1", "crew.days.few", "crew.days"),
]
WITHOUT_ONE = [
    (None, "crew.patches.few", "crew.patches"),
    (None, "crew.ago.days.few", "crew.ago.days"),
    (None, "crew.ago.weeks.few", "crew.ago.weeks"),
]
FAMILIES = WITH_ONE + WITHOUT_ONE

# The languages here with a distinct category for 2, 3 and 4.
HAS_FEW = {"ru", "uk", "pl"}


@pytest.mark.parametrize("loc", sorted(TRANSLATIONS))
def test_a_language_with_no_few_category_has_no_few_wording(loc):
    """Why the 2-3-4 branch may be run for every language.

    There is no locale check in the code -- there is this. If a translator ever gives German a
    different wording for 2 than for 7, German silently starts using the Slavic branch and
    nothing else here would notice.
    """
    if loc in HAS_FEW:
        return
    split = [(few, TRANSLATIONS[loc][few], TRANSLATIONS[loc][many])
             for _, few, many in FAMILIES
             if TRANSLATIONS[loc][few] != TRANSLATIONS[loc][many]]
    assert not split, (
        f"{loc} has no separate wording for 2-4, but {len(split)} families give one anyway, "
        f"so the Slavic branch now decides for this language too: "
        + "; ".join(f"{k}: {a!r} vs {b!r}" for k, a, b in split))


def cldr(loc, n):
    """The category CLDR gives this language for this whole number."""
    d, h = n % 10, n % 100
    if loc in ("ru", "uk"):
        if d == 1 and h != 11:
            return "one"
        if 2 <= d <= 4 and not (12 <= h <= 14):
            return "few"
        return "many"
    if loc == "pl":
        if n == 1:
            return "one"
        if 2 <= d <= 4 and not (12 <= h <= 14):
            return "few"
        return "many"
    return "one" if n == 1 else "many"


NUMBERS = [1, 2, 3, 4, 5, 7, 11, 12, 14, 15, 21, 22, 24, 25, 31, 44, 101, 111, 112, 121, 201]

HARNESS = """
const T = %s;
let LOC = "en";
global.document = { documentElement: { get lang() { return LOC; } } };
function t(k, vars) {
  return String(T[LOC][k]).replace(/\\{(\\w+)\\}/g,
    (m, p) => (vars && vars[p] != null ? vars[p] : m));
}
%s
const FAM = %s;
const out = [];
for (const loc of Object.keys(T)) {
  LOC = loc;
  for (const n of %s) {
    for (const [one, few, many] of FAM) out.push([loc, n, few, plural(one, few, many, n)]);
  }
}
console.log(JSON.stringify(out));
"""


def _run(families):
    src = CREWS_JS.read_text(encoding="utf-8")
    start = src.index("  var ONE_AT_X1 = ")
    end = src.index("  // Area follows the same metric/imperial switch")
    tabs = {loc: {k: v for k, v in TRANSLATIONS[loc].items() if k.startswith("crew.")}
            for loc in TRANSLATIONS}
    tabs["en"] = {k: v for k, v in EN.items() if k.startswith("crew.")}
    d = pathlib.Path(tempfile.mkdtemp(prefix="plural-"))
    try:
        f = d / "t.js"
        f.write_text(HARNESS % (json.dumps(tabs, ensure_ascii=False), src[start:end],
                                json.dumps(families), json.dumps(NUMBERS)),
                     encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True,
                           encoding="utf-8")
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _wrong(families):
    tabs = dict(TRANSLATIONS)
    tabs["en"] = EN
    by_few = {few: (one, many) for one, few, many in families}
    out = []
    for loc, n, few, got in _run(families):
        one, many = by_few[few]
        # A family with no singular wording is never handed 1 -- both of its callers guard
        # with `> 1` -- so 1 is not a case it can get wrong.
        if one is None and n == 1:
            continue
        cat = cldr(loc, n)
        key = {"one": one, "few": few, "many": many}[cat]
        if key is None:
            out.append(f"{loc} n={n} {few}: {cat} is wanted and no singular wording exists; "
                       f"it printed {got!r}")
            continue
        want = tabs[loc][key].replace("{n}", str(n))
        if got != want:
            out.append(f"{loc} n={n} {few}: got {got!r}, {cat} wants {want!r}")
    return out


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
def test_each_language_gets_the_wording_its_own_rules_ask_for():
    wrong = _wrong(WITH_ONE)
    assert not wrong, (f"{len(wrong)} numbers are printed in the wrong form:" + NL
                       + NL.join("  " + w for w in wrong[:14]))


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped js")
@pytest.mark.xfail(strict=True, reason="needs crew.patch1 and crew.ago.week1 in all nineteen"
                                       " tables, through the translation workflow")
def test_the_families_with_no_singular_cannot_give_one_at_twenty_one():
    """Pinned as broken, not as fine.

    Both callers guard with `> 1`, so 1 itself never arrives and the missing wording costs
    nothing there. 21 does arrive -- a square held 21 weeks, a crew spread over 21 patches --
    and Russian and Ukrainian want the singular for it. Writing nineteen noun forms by hand is
    how a round of translation fixes went wrong before, so this waits for a regeneration pass
    and fails here until then. If it ever passes, the keys landed: delete the marker.
    """
    assert not _wrong(WITHOUT_ONE)
