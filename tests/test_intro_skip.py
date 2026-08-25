"""Clicking or pressing Esc skips the intro by running it to its end.

The video is short (about 6 s) and has two modes already: a full first view, and a repeat
view that seeks to the final second. Cutting the video off would have meant a third path
through that logic. Seeking into the last second instead means the existing ending plays,
`onended` fires as it always did, and neither mode nor the "no intro" setting is touched.
"""
import re

from fastapi.testclient import TestClient

from main import app


def _page() -> str:
    with TestClient(app) as client:
        return client.get("/").text


def _skip_js() -> str:
    m = re.search(r"const skip=e=>\{.*?\n    \};", _page(), re.S)
    assert m, "skip handler not found"
    return m.group(0)


def test_skip_seeks_into_the_last_second():
    js = _skip_js()
    assert "vid.duration-1.0" in js
    assert "vid.currentTime=target" in js


def test_skip_never_rewinds():
    """In repeat mode the video is already near the end; a skip must not send it backwards."""
    assert "target>vid.currentTime" in _skip_js()


def test_skip_falls_back_when_it_cannot_seek():
    """Metadata not loaded yet, so there is no duration to seek to: end it instead."""
    assert "if(!seeked) endVideo();" in _skip_js()


def test_esc_skips_but_other_keys_do_not():
    js = _skip_js()
    assert 'e.key==="Escape"' in js
    assert 'e.type==="keydown"&&!(' in js, "every keypress would skip"


def test_listeners_are_removed_when_the_intro_ends():
    page = _page()
    assert "skipOff=()=>{removeEventListener(\"pointerdown\",skip);removeEventListener(\"keydown\",skip);}" in page
    assert "if(skipOff){skipOff();skipOff=null;}" in page


def test_both_existing_modes_are_untouched():
    """The two-mode logic and the off-switch must be exactly as they were."""
    page = _page()
    assert "setTimeout(endVideo,14000)" in page          # full first view
    assert "vid.currentTime=vid.duration-0.9" in page    # repeat view seek
    assert "setTimeout(endVideo,4500)" in page           # repeat view safety net
    assert "if(_C.intro_enabled===false||_introOff){" in page   # off entirely


def test_the_single_exit_path_is_still_single():
    page = _page()
    assert "videoDone) return; videoDone=true" in page
    assert page.count("doIntro(); };") == 1
