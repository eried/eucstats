"""The admin console's second factor: the phone, on top of the authenticator code.

Admin was a single TOTP code. That is one factor — something you know, or more precisely
something your authenticator app knows — and it guards the console that can ban riders, switch
datasets and delete accounts. So crews brought a second one with it: the console now also
wants a pairing confirmed from the admin's own rider identity, the same QR scan ordinary riders
use, which is something you have.

What is explicitly NOT the mechanism
------------------------------------
Signing in as "whoever holds this store_id" would have been easier and would have been worse.
A store_id is a bearer string that travels in every single upload; it is a username, not a
password. On its own it must never open the admin console. It is one of two factors here and
never the only one, and the pairing that proves possession of the phone expires in minutes
while the store_id does not.

Trust on first use
------------------
The first pairing to complete against a freshly enrolled console claims it, and its store_id
is written to `admin.json`. After that no other rider can pair as admin. That avoids asking
anybody to type a long opaque identifier into a form, which is both unpleasant and the exact
habit this is trying not to build. Changing it afterwards means editing that file on the
server, which is the right amount of difficulty for the operation.
"""
from __future__ import annotations

import json

import config
from services import pairing

STATE_FILE = config.ADMIN_STATE_FILE


def _load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def bound_store_id() -> str | None:
    """The rider identity this console is bound to, or None before first use."""
    return _load().get("admin_store_id")


def second_factor_required() -> bool:
    """Whether the phone factor is enforced.

    A switch, not a belief. If the bound phone is lost the console would otherwise be
    unreachable, and the recovery path has to be something the owner can do over SSH with no
    second device: set this false in `admin.json`, sign in with TOTP alone, pair a new phone,
    set it back. It defaults to on and nothing in the web interface can turn it off — a
    console that can be downgraded to one factor from inside the console has one factor.
    """
    return bool(_load().get("admin_require_pairing", True))


def claim_or_check(store_id: str) -> bool:
    """True if this rider may act as admin. Claims the console if nothing has yet."""
    state = _load()
    bound = state.get("admin_store_id")
    if not bound:
        state["admin_store_id"] = store_id
        _save(state)
        return True
    return bound == store_id


def start_pairing(db) -> dict:
    """Open an admin-purpose pairing. `purpose` keeps it out of the rider path: an admin QR
    scanned by the app says so on screen, and a rider QR can never yield admin scope."""
    return pairing.start(db, purpose="admin")


def check_pairing(db, token: str) -> dict:
    """Poll an admin pairing. Returns {"status": "paired"} only for the bound rider.

    The scope check is the whole point and it was missing: `poll()` returns status "paired"
    for a RIDER pairing too, so without this an attacker who had the TOTP code could satisfy
    the "second factor" with two public API calls and no phone at all — which made the second
    factor not a factor. The purpose is pinned at both ends: the token must have been minted
    with purpose="admin" (see start_pairing), and the poll must report admin scope.
    """
    res = pairing.poll(db, token)
    if res.get("status") != "paired":
        return res
    if res.get("scope") != "admin":
        return {"status": "denied", "reason": "not_an_admin_pairing"}
    sid = res.get("store_id")
    if not sid or not claim_or_check(sid):
        return {"status": "denied", "reason": "not_admin"}
    return {"status": "paired", "store_id": sid}


def is_authenticated(request) -> bool:
    """Both factors, in one browser session."""
    sess = request.session
    if not sess.get("admin_auth", False):
        return False
    if not second_factor_required():
        return True
    return bool(sess.get("admin_paired", False))


def stage(request) -> str:
    """Which step the sign-in is on: 'totp', 'pair' or 'done'."""
    if not request.session.get("admin_auth", False):
        return "totp"
    if second_factor_required() and not request.session.get("admin_paired", False):
        return "pair"
    return "done"
