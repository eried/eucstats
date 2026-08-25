"""The public page's init() is executed, not just syntax-checked.

A `const` declared inside a block is invisible to a function defined outside it, and calling
it there is a ReferenceError at RUNTIME. `node --check` passes such a file happily. That
exact mistake once left every visitor looking at a map with no chrome, because the error
killed init() before it could reveal anything.

This runs the real served script under stub browser APIs and asserts the intro reaches
runIntro() - the point where the UI appears - in each of the three ways the intro can end.
Skipped when node is unavailable.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

HARNESS = Path(__file__).parent / "intro_harness.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _page_script(tmp_path: Path) -> Path:
    with TestClient(app) as client:
        html = client.get("/").text
    script = max(re.findall(r"<script>(.*?)</script>", html, re.S), key=len)
    p = tmp_path / "app.js"
    p.write_text(script, encoding="utf-8")
    return p


def _run(tmp_path: Path, scenario: str) -> dict:
    js = _page_script(tmp_path)
    out = subprocess.run(["node", str(HARNESS), str(js), scenario],
                         capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, f"harness failed: {out.stderr[-800:]}"
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("scenario", ["natural", "skip", "off"])
def test_init_does_not_throw(tmp_path, scenario):
    r = _run(tmp_path, scenario)
    assert r["threw"] is None, f"init() threw in the {scenario} case: {r['threw']}"


@pytest.mark.parametrize("scenario", ["natural", "skip", "off"])
def test_the_chrome_is_revealed(tmp_path, scenario):
    """runIntro() is what makes the topbar, dock and gear appear."""
    r = _run(tmp_path, scenario)
    assert r["revealed"], f"the UI never appeared in the {scenario} case"


def test_a_skip_seeks_towards_the_end(tmp_path):
    r = _run(tmp_path, "skip")
    assert r["seekedTo"] and r["seekedTo"] > 5.0, f"skip did not seek: {r['seekedTo']}"
