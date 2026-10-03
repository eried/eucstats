"""The page a camera app opens, in all nineteen languages and in both of its states.

I localised this page in one commit and broke it in the same commit. `t()` interpolated the
`<b>` wrapper into the sentence and then ran `escape()` over the whole result, so the page
served `Say yes in &lt;b&gt;EUC Planet&lt;/b&gt;` -- the tags as visible text -- in every
language it had just learned. Nothing noticed for a round, because the dev server had not
restarted since before the change and every browser that looked at the page, mine and three
reviewers', was served the old hard-coded English.

That is the class: a template whose variables are HTML, escaped in the wrong order, or not
filled at all. Both failures look the same from outside -- a token on the screen where a
sentence belongs -- and both are invisible to a test that only checks the status code.

So: render both states in all nineteen locales and fail on an escaped tag, on a `__TOKEN__`
nobody replaced, on a `{name}` nobody filled, and on a dead code still offering a handshake.
"""
import re

import pytest
from fastapi.testclient import TestClient

from services import pairing, settings

_CREW_DEFAULTS = dict(enabled=True, zoom=14, window_days=90, seed=2, cooldown_days=7,
                      max_members=0, opacity=0.55, creation_open=True)


@pytest.fixture
def client(db):
    from main import app
    settings.set_crews(db, **_CREW_DEFAULTS)
    return TestClient(app)


def _locales():
    from web import i18n
    return ["en"] + sorted(i18n.TRANSLATIONS)


def _get(client, code, loc):
    r = client.get("/p/" + code, headers={"accept-language": loc})
    assert r.status_code == 200, f"{loc}: {r.status_code}"
    return r.text


# An escaped tag is the bug I shipped; `&amp;` is legitimate and appears in the deep link.
ESCAPED_TAG = re.compile(r"&lt;/?[a-z]")
LEFTOVER_TOKEN = re.compile(r"__[A-Z]+__")
LEFTOVER_VAR = re.compile(r"\{(?:app|crews|name|n|v)\}")


@pytest.mark.parametrize("loc", _locales())
def test_a_live_code_renders_without_a_tag_or_a_token_on_the_screen(client, db, loc):
    p = pairing.start(db, purpose="rider")
    page = _get(client, p["code"], loc)
    m = ESCAPED_TAG.search(page)
    assert not m, (f"{loc}: an HTML tag is painted as text at {m.start()}: "
                   f"{page[max(0, m.start() - 60):m.start() + 40]!r}. "
                   f"Escape the translation, then substitute the markup into it.")
    m = LEFTOVER_TOKEN.search(page)
    assert not m, f"{loc}: {m.group(0)} was never replaced"
    m = LEFTOVER_VAR.search(page)
    assert not m, f"{loc}: the placeholder {m.group(0)} was never filled"
    assert p["code"] in page, f"{loc}: the code itself is missing"
    assert "eucplanet://pair?code=" + p["code"] in page, f"{loc}: no deep link"


@pytest.mark.parametrize("loc", _locales())
def test_a_dead_code_says_so_and_offers_nothing(client, db, loc):
    """`/p/ZZZZZZ` used to render byte-identical to a live one.

    A code lasts three minutes and works once, so a scan of a photographed or screenshotted
    QR is likelier to be stale than live -- and those riders were shown the heading, the code,
    the button and the safety notice, and handed their app a code that could not work.
    """
    page = _get(client, "ZZZZZZ", loc)
    m = ESCAPED_TAG.search(page)
    assert not m, f"{loc}: an HTML tag is painted as text: {m.group(0)!r}"
    m = LEFTOVER_TOKEN.search(page)
    assert not m, f"{loc}: {m.group(0)} was never replaced"
    m = LEFTOVER_VAR.search(page)
    assert not m, f"{loc}: the placeholder {m.group(0)} was never filled"
    assert "eucplanet://" not in page, f"{loc}: a dead code still offers the app a handshake"
    assert "ZZZZZZ" not in page, f"{loc}: a dead code is still displayed as if it worked"


@pytest.mark.parametrize("loc", _locales())
def test_both_states_carry_a_way_back_to_the_site(client, db, loc):
    """There was one control on this page and it was a `eucplanet://` link.

    With no app installed, or a code that had rolled, the page was a cul-de-sac that never
    mentioned the site it was served from.
    """
    p = pairing.start(db, purpose="rider")
    for name, code in (("live", p["code"]), ("dead", "ZZZZZZ")):
        page = _get(client, code, loc)
        assert 'href="http://testserver/"' in page, f"{loc} {name}: no link back to the site"


def test_the_page_declares_the_language_it_answered_in(client, db):
    """`lang=en` was hard-coded while the body came back in the reader's language."""
    from web import i18n
    for loc in _locales():
        page = _get(client, "ZZZZZZ", loc)
        assert f"<html lang={i18n.pick(loc)}>" in page, f"{loc}: wrong lang attribute"
