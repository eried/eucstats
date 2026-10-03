"""The stylesheet may not contradict itself.

A later rule beating an earlier one at the same specificity, from a context that does not
narrow it, is how this feature shipped a phone board whose week column was hidden and shown
by two rules in one block, and a two-line phone row that was dead code because a base rule
sixty lines further down overrode it. Neither was visible in review; both were visible to a
script. It runs in the suite now rather than in a scratchpad.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_css_rule_silently_cancels_an_earlier_one():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_crews_css.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
