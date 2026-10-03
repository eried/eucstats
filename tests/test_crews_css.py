"""The stylesheet may not contradict itself, and the checker has to be able to tell.

A later rule beating an earlier one at the same specificity, from a context that does not
narrow it, is how this feature shipped a phone board whose week column was hidden and shown by
two rules in one block, and a two-line phone row that was dead code because a base rule sixty
lines further down overrode it. Neither was visible in review; both were visible to a script.

The second test exists because a reviewer proved the script blind to the exact bug it had been
written for the round before. It keyed on the literal selector string, so `.crewtag` and
`.crewtag.kills` were two different keys and were never compared: the phone rule meant to
strip every chip's tinted box is one class, the rules that painted those boxes are two, and
media queries add nothing to specificity, so the phone rule could not win wherever it sat.
They deleted the fix, re-ran the script, and it reported no problems at all. A checker that
passes a file containing the bug it was written for is worse than no checker, because it gets
quoted as evidence -- so the proof is a test now.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_crews_css.py"
CSS = ROOT / "web" / "static" / "crews.css"

# The rule that makes every coloured chip lose its box on a phone. Removing it reintroduces
# the round-six bug exactly.
PHONE_STRIP = """  .crewtag.first, .crewtag.joins, .crewtag.kills, .crewtag.drops,
  .crewtag.done, .crewtag.youpass { background: none; padding: 0; }"""


def _run(checker):
    return subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)


def test_no_css_rule_silently_cancels_an_earlier_one():
    r = _run(CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_checker_catches_a_rule_that_can_never_win(tmp_path):
    """Delete the phone fix and the checker has to notice. This is the reviewer's own proof."""
    css = CSS.read_text(encoding="utf-8")
    assert PHONE_STRIP in css, "the rule under test moved; update PHONE_STRIP"
    broken = tmp_path / "crews.css"
    broken.write_text(css.replace(PHONE_STRIP, "", 1), encoding="utf-8")

    probe = tmp_path / "check.py"
    src = CHECKER.read_text(encoding="utf-8")
    src, n = re.subn(r"^CSS = pathlib\.Path\(__file__\).*$",
                     f"CSS = pathlib.Path(r{str(broken)!r})", src, count=1, flags=re.M)
    assert n == 1, "could not point the checker at a copy; its CSS path line changed"
    probe.write_text(src, encoding="utf-8")

    r = _run(probe)
    assert r.returncode == 1, (
        "the checker passed a stylesheet with the bug it exists to catch:\n" + r.stdout)
    assert "crewtag" in r.stdout, r.stdout
