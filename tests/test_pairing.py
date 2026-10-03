"""The handshake that stands between a browser and somebody's riding history.

This had no test of any kind. Every other suite that needed a signed-in rider switched
pairing off (`admin_require_pairing = False`) and carried on, so the one piece of the feature
that decides who you are was the one piece nothing exercised. These are the properties it has
to keep: a code is single-use, short-lived, scoped to what it was opened for, and useless to
anybody who did not open it.
"""
from datetime import timedelta

import pytest

from models import Rider, WebSession, utcnow
from services import pairing


def _rider(db, sid="pr1"):
    db.add(Rider(store_id=sid, display_name=sid.title(), flag="NO"))
    db.commit()
    return sid


def test_a_code_opens_and_the_app_approves_it_and_the_browser_gets_a_session(db):
    sid = _rider(db)
    p = pairing.start(db)
    assert len(p["code"]) == pairing.CODE_LEN
    assert pairing.poll(db, p["token"])["status"] == "waiting"
    pairing.confirm(db, p["code"], sid)
    out = pairing.poll(db, p["token"])
    assert out["status"] == "paired"
    assert out["session"]
    ws = pairing.session(db, out["session"])
    assert ws is not None and ws.store_id == sid


def test_the_code_is_not_the_session_and_the_token_is_not_the_code(db):
    """The screen shows the code and the browser holds the token. Anyone who can read the
    screen, including over a shoulder or in a photograph, knows the code; that must buy them
    nothing, because the session is handed to whoever holds the token."""
    sid = _rider(db)
    p = pairing.start(db)
    pairing.confirm(db, p["code"], sid)
    with pytest.raises(pairing.PairError):
        pairing.poll(db, p["code"])            # the code is not a token
    assert pairing.poll(db, p["token"])["session"]


def test_a_code_is_good_once(db):
    sid = _rider(db, "once")
    p = pairing.start(db)
    pairing.confirm(db, p["code"], sid)
    assert pairing.poll(db, p["token"])["status"] == "paired"
    with pytest.raises(pairing.PairError):
        pairing.confirm(db, p["code"], sid)


def test_an_expired_code_cannot_be_approved(db):
    sid = _rider(db, "slow")
    p = pairing.start(db)
    tok = pairing._live(db, p["code"])
    tok.created_at = utcnow() - pairing.CODE_TTL - timedelta(seconds=5)
    db.commit()
    with pytest.raises(pairing.PairError):
        pairing.confirm(db, p["code"], sid)
    with pytest.raises(pairing.PairError):
        pairing.poll(db, p["token"])


def test_an_unknown_code_is_refused_without_saying_why(db):
    with pytest.raises(pairing.PairError):
        pairing.describe(db, "ZZZZZZ")
    with pytest.raises(pairing.PairError):
        pairing.confirm(db, "ZZZZZZ", "nobody")


def test_the_app_is_shown_what_it_is_approving(db):
    p = pairing.start(db)
    d = pairing.describe(db, p["code"])
    assert d["purpose"] == "rider"
    assert d["expires_in"] > 0


def test_a_rider_session_is_not_an_admin_session(db):
    """`purpose` is the whole reason the admin screen cannot be reached with a pass minted
    for the crew panel. A session scoped to one must not answer for the other."""
    sid = _rider(db, "scoped")
    p = pairing.start(db, purpose="rider")
    pairing.confirm(db, p["code"], sid)
    s = pairing.poll(db, p["token"])["session"]
    assert pairing.session(db, s, scope="crew") is not None
    assert pairing.session(db, s, scope="admin") is None


def test_a_session_that_has_not_been_used_in_a_season_is_dead(db):
    sid = _rider(db, "stale")
    p = pairing.start(db)
    pairing.confirm(db, p["code"], sid)
    s = pairing.poll(db, p["token"])["session"]
    ws = db.query(WebSession).filter(WebSession.session_id == s).one()
    ws.last_used = utcnow() - pairing.SESSION_TTL - timedelta(days=1)
    db.commit()
    assert pairing.session(db, s) is None


def test_signing_out_ends_that_session_and_only_that_one(db):
    sid = _rider(db, "two")
    out = []
    for _ in range(2):
        p = pairing.start(db)
        pairing.confirm(db, p["code"], sid)
        out.append(pairing.poll(db, p["token"])["session"])
    pairing.revoke(db, out[0])
    assert pairing.session(db, out[0]) is None
    assert pairing.session(db, out[1]) is not None


def test_a_rider_can_end_every_session_they_have(db):
    sid = _rider(db, "all")
    for _ in range(3):
        p = pairing.start(db)
        pairing.confirm(db, p["code"], sid)
        pairing.poll(db, p["token"])
    assert pairing.revoke_all(db, sid) >= 3
    assert db.query(WebSession).filter(WebSession.store_id == sid).count() == 0


def test_no_session_at_all_is_simply_nobody(db):
    assert pairing.session(db, None) is None
    assert pairing.session(db, "") is None
    assert pairing.session(db, "not-a-session") is None


def test_codes_avoid_the_characters_people_read_wrong(db):
    """A code is read off one screen and typed into another phone, often outdoors, which is
    where O against 0 and I against 1 go wrong."""
    seen = "".join(pairing._code() for _ in range(300))
    assert not (set(seen) & set("OI01"))
    assert set(seen) <= set(pairing.ALPHABET)



def test_a_handle_is_not_accepted_as_proof_of_identity(db):
    """The API publishes a rider's handle in place of their store_id, which is only safe for
    as long as a handle buys nothing at this door."""
    from conftest import HANDLE
    sid = _rider(db, "probe")
    p = pairing.start(db)
    with pytest.raises(pairing.PairError):
        pairing.confirm(db, p["code"], HANDLE(sid))
