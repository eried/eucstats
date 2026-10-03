"""The stylesheet may not contradict itself, and the checker has to be able to tell.

A later rule beating an earlier one at the same specificity, from a context that does not
narrow it, is how this feature shipped a phone board whose week column was hidden and shown by
two rules in one block, and a two-line phone row that was dead code because a base rule sixty
lines further down overrode it. Neither was visible in review; both were visible to a script.

The rest of this file exists because reviewers twice proved the script blind to the exact bug
it had been written for. The first time it keyed on the literal selector string, so `.crewtag`
and `.crewtag.kills` were never compared. The second time they got past it four separate ways
in one sitting -- and made it fail two stylesheets that were perfectly correct -- while the
shipped file contained a live instance of the class it guards: a phone rule relaxing
`.crewtwho` while `white-space: nowrap; overflow: hidden` sat on `.crewtwho i`, so the card
painted 62px outside its own border and the script reported no problems.

A checker that passes a file containing the bug it was written for is worse than no checker,
because it gets quoted as evidence. So every bypass and every false alarm is pinned here.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_crews_css.py"
CSS = ROOT / "web" / "static" / "crews.css"

# The rule that makes every coloured chip lose its box on a phone. Removing it reintroduces
# the round-six bug exactly.
PHONE_STRIP = """  .crewtag.first, .crewtag.joins, .crewtag.kills, .crewtag.drops,
  .crewtag.done, .crewtag.youpass { background: none; padding: 0; }"""

TRUNCATE = (".crewtwho i { display: block; font-style: normal; font-size: 11px;"
            " line-height: 1.25; }")
KILLS = ".crewtag.kills { color: #ff4fa3; background: rgba(255,79,163,.16); }"
NL = chr(10)


def _run(checker):
    return subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)


def _point_at(tmp_path, css_text):
    """A copy of the checker aimed at a copy of the stylesheet."""
    broken = tmp_path / "crews.css"
    broken.write_text(css_text, encoding="utf-8")
    src, n = re.subn(r"^CSS = pathlib\.Path\(__file__\).*$",
                     "CSS = pathlib.Path(r" + repr(str(broken)) + ")",
                     CHECKER.read_text(encoding="utf-8"), count=1, flags=re.M)
    assert n == 1, "could not point the checker at a copy; its CSS path line changed"
    probe = tmp_path / "check.py"
    probe.write_text(src, encoding="utf-8")
    return probe


def test_no_css_rule_silently_cancels_an_earlier_one():
    r = _run(CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr


def _cases():
    """(name, mutated stylesheet, expected exit code)."""
    css = CSS.read_text(encoding="utf-8")
    second_block = (NL + "@media (max-width: 560px), (hover: none) and (pointer: coarse) {" + NL
                    + "  .crewtat, .crewtwho i { display: block; }" + NL + "}" + NL)
    truncating = (".crewtwho i { display: block; font-style: normal; font-size: 11px;" + NL
                  + "  line-height: 1.25; white-space: nowrap; overflow: hidden;" + NL
                  + "  text-overflow: ellipsis; }")
    return [
        # --- four ways a reviewer got past it. Each must now exit 1.
        ("the phone chip-strip deleted",
         css.replace(PHONE_STRIP, "", 1), 1),
        ("the strip written as a longhand against a shorthand",
         css.replace(PHONE_STRIP, "  .crewtag { background-color: transparent; }", 1), 1),
        ("the chips painted through an attribute selector",
         css.replace(KILLS, KILLS.replace(".crewtag.kills", ".crewtag[data-fam=kills]"), 1)
            .replace(PHONE_STRIP, "", 1), 1),
        ("a second phone block worded differently",
         css + second_block, 1),
        # Both halves of the defect that shipped: a child that truncates, and a phone rule
        # relaxing the PARENT in the belief that it reaches the child. Injected together,
        # because the stylesheet no longer contains either half on its own.
        ("a parent trying to relax a child's overflow",
         css.replace(TRUNCATE, truncating, 1)
            + NL + "@media (max-width: 560px) {" + NL
            + "  .crewtwho { white-space: normal; overflow: visible; text-overflow: clip; }"
            + NL + "}" + NL, 1),
        # --- four more a reviewer got past it with, in the third round running
        ("a media query differing by one space",
         css + NL + "@media (max-width:560px) { .crewtat { display: block; } }" + NL, 1),
        ("one ancestor step on the narrow selector",
         css.replace(TRUNCATE,
                     ".crewcard .crewtwho i { display: block; white-space: nowrap;" + NL
                     + "  overflow: hidden; text-overflow: ellipsis; }", 1)
            + NL + "@media (max-width: 560px) {" + NL
            + "  .crewtwho { white-space: normal; overflow: visible; }" + NL + "}" + NL, 1),
        ("a logical property against its physical twin",
         css + NL + ".zzrow.wide { padding-left: 9px; }" + NL
             + "@media (max-width: 560px) { .zzrow { padding-inline-start: 0; } }" + NL, 1),
        (":is() hiding a selector from the parser",
         css + NL + ".zztag.hot { background: #f00; }" + NL
             + "@media (max-width: 560px) { :is(.zztag, .zzother) { background: none; } }" + NL,
         1),
        # --- two correct stylesheets it used to fail. Each must exit 0.
        ("!important, which genuinely wins",
         css.replace(PHONE_STRIP,
                     "  .crewtag { background: none !important; padding: 0 !important; }", 1), 0),
        ("a fix written as a descendant selector",
         css.replace(PHONE_STRIP, "  .crewtrow .crewtag { background: none; padding: 0; }", 1), 0),
    ]


@pytest.mark.parametrize("idx", range(11))
def test_the_checker_is_not_fooled_and_does_not_cry_wolf(idx, tmp_path):
    name, mutated, want = _cases()[idx]
    assert mutated != CSS.read_text(encoding="utf-8"), (
        name + ": the mutation changed nothing, so its anchor has moved")

    r = _run(_point_at(tmp_path, mutated))
    if want:
        assert r.returncode == 1, name + ": slipped past the checker" + NL + r.stdout
    else:
        assert r.returncode == 0, name + ": correct CSS reported as broken" + NL + r.stdout
