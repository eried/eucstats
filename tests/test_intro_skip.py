"""The intro can be skipped by clicking or pressing Esc.

Skipping is deliberately not a new code path: `endVideo` is already the single, idempotent
exit (guarded by `videoDone`) shared by the video ending, erroring, and the safety timeout.
A skip is a fourth trigger for it, so the reveal, the "seen" flag and the map-ready gate all
behave exactly as they already did.
"""
import re

from fastapi.testclient import TestClient

from main import app


def _intro_js() -> str:
    with TestClient(app) as client:
        page = client.get("/").text
    m = re.search(r"let keySkip=null;.*?addEventListener\(\"keydown\",keySkip\);", page, re.S)
    assert m, "intro skip block not found"
    return m.group(0)


def test_a_click_skips():
    assert 'vid.addEventListener("pointerdown",endVideo)' in _intro_js()


def test_escape_skips():
    js = _intro_js()
    assert 'e.key==="Escape"' in js


def test_the_key_listener_is_removed_when_the_intro_ends():
    """It is on window, so it has to be cleaned up; the video element removes itself."""
    js = _intro_js()
    assert 'removeEventListener("keydown",keySkip)' in js


def test_skip_reuses_the_single_exit_path():
    """No second copy of the teardown: everything still goes through endVideo."""
    js = _intro_js()
    assert "videoDone) return; videoDone=true" in js, "the idempotent guard is gone"
    assert js.count("doIntro();") == 1, "teardown was duplicated instead of reused"


def test_the_map_ready_gate_is_untouched():
    """Skipping early must not reveal a half-built map."""
    with TestClient(app) as client:
        page = client.get("/").text
    assert "if(introRan||!mapReady||!videoDone) return;" in page
