"""Signing in without a login: the phone vouches for the browser.

There is no password anywhere in eucstats, and adding one for crews would have meant inventing
an account system, an email flow and a reset flow for a feature about riding in a group. The
app already holds the one durable identifier a rider has — the store_id it puts on every
upload — so the browser borrows it for a moment instead of storing it.

How it goes
-----------
1. The browser asks for a pairing. It gets a long random **token** it keeps to itself, and a
   short **code** it shows on screen as a QR (and in text, for anyone who would rather type).
2. The app scans the QR and confirms the code. It is the app that sends the store_id — the
   browser never sees it, never asks for it, and cannot make one up.
3. The browser, still polling with its token, receives an opaque session id.

Why the store_id is not simply typed into the browser
-----------------------------------------------------
Because it is a bearer string that travels in every upload. A rider who pasted it into a web
form would be handing over their whole upload identity to anything watching, and a leaked one
would let anybody claim their rides. The pairing turns a permanent secret into a scoped,
expiring session that can be revoked, and the secret itself stays on the phone.

The attack this has to survive
------------------------------
Device-code flows have a known weakness: an attacker starts a pairing on their own machine and
persuades somebody else's app to approve it, which hands the attacker a session for the
victim. Four things make that hard here, and the first two are the important ones:

* **The app shows what is being approved** before it approves anything — which rider, and
  where the browser is — because a confirmation screen that only says "Allow?" trains people
  to say yes.
* **A code lives three minutes and is used once.** There is no window in which to pass one
  around.
* Codes are drawn from an alphabet with no look-alike characters, so nobody mistypes into
  somebody else's pairing.
* Confirmations are rate-limited per rider and per address, so codes cannot be guessed.

Scope
-----
A paired session is `crew` scope: it can create, join and leave crews and act on members. It
cannot upload a ride, rename a rider, download anything or delete an account — those stay with
the app and the store_id it holds. `admin` scope exists too, and it is never granted by
pairing alone: see `services/adminauth.py`.
"""
from __future__ import annotations

import base64
import secrets
from datetime import timedelta
from io import BytesIO

from models import PairToken, Rider, WebSession, utcnow

# No I, O, 0 or 1: a code is read off one screen and typed into another, sometimes by someone
# holding a phone in the other hand.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 6
CODE_TTL = timedelta(minutes=3)
SESSION_TTL = timedelta(days=90)        # renewed on use, so an active rider never signs in twice
COOKIE = "crew_session"


class PairError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def _code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def start(db, purpose: str = "rider") -> dict:
    """Open a pairing. The caller keeps `token`; `code` is the part shown to the human."""
    for _ in range(20):                 # a free code: collisions are possible, not a failure
        code = _code()
        if not _live(db, code):
            break
    else:
        raise PairError("busy", "Too many pairings in flight. Try again in a minute.")
    token = secrets.token_urlsafe(32)
    db.add(PairToken(token=token, code=code, purpose=purpose))
    db.commit()
    return {"token": token, "code": code, "expires_in": int(CODE_TTL.total_seconds())}


def _live(db, code: str) -> PairToken | None:
    """An unused, unexpired pairing for this code."""
    cutoff = utcnow() - CODE_TTL
    return (db.query(PairToken)
            .filter(PairToken.code == code.strip().upper(),
                    PairToken.used_at.is_(None),
                    PairToken.created_at >= cutoff)
            .order_by(PairToken.created_at.desc())
            .first())


def describe(db, code: str) -> dict:
    """What the app shows before it asks the rider to approve anything.

    A confirmation screen that only says "Allow?" is a screen people learn to tap through, so
    the app is given something specific to display: that this is a crew sign-in, and that it
    expires in a moment.
    """
    pt = _live(db, code)
    if pt is None:
        raise PairError("expired", "That code has expired. Ask for a new one.")
    age = (utcnow() - pt.created_at).total_seconds()
    return {"purpose": pt.purpose, "scope": "admin" if pt.purpose == "admin" else "crew",
            "expires_in": max(0, int(CODE_TTL.total_seconds() - age)),
            "grants": ["crew_membership"] if pt.purpose == "rider" else ["admin_console"]}


def confirm(db, code: str, store_id: str) -> dict:
    """The app's side: this code belongs to this rider. Marks the pairing used."""
    pt = _live(db, code)
    if pt is None:
        raise PairError("expired", "That code has expired. Ask for a new one.")
    rider = db.get(Rider, store_id)
    if rider is None or rider.deleted_at is not None:
        raise PairError("no_rider", "That rider is not registered.")
    pt.store_id = store_id
    pt.used_at = utcnow()
    db.commit()
    return {"ok": True, "purpose": pt.purpose}


def poll(db, token: str) -> dict:
    """The browser's side. Returns a session id once the app has confirmed.

    The session is created here rather than at confirm time so it is handed to the browser
    that started the pairing and to nobody else: the token never leaves it.
    """
    pt = db.get(PairToken, token)
    if pt is None:
        raise PairError("unknown", "Unknown pairing.")
    if pt.used_at is None:
        age = (utcnow() - pt.created_at).total_seconds()
        if age > CODE_TTL.total_seconds():
            raise PairError("expired", "That code expired before it was scanned.")
        return {"status": "waiting", "expires_in": int(CODE_TTL.total_seconds() - age)}
    if not pt.store_id:
        raise PairError("expired", "That pairing was not completed.")
    existing = (db.query(WebSession)
                .filter(WebSession.store_id == pt.store_id,
                        WebSession.scope == "crew").first())
    if pt.purpose == "admin":
        # Pairing proves possession of the phone; it is only ever one of the two admin
        # factors. Consumed here like any other: the admin branch used to return before the
        # delete below, so a confirmed admin token answered "paired" forever — it never
        # expired once used, and it travels in a URL query string, which means proxy logs
        # and browser history held a replayable admin factor.
        store = pt.store_id
        db.delete(pt)
        db.commit()
        return {"status": "paired", "store_id": store, "scope": "admin"}
    sid = secrets.token_urlsafe(32)
    db.add(WebSession(session_id=sid, store_id=pt.store_id, scope="crew"))
    db.delete(pt)                        # one use, and nothing left to replay
    db.commit()
    return {"status": "paired", "session": sid, "store_id": pt.store_id, "scope": "crew",
            "reused": existing is not None}


def session(db, sid: str | None, scope: str = "crew") -> WebSession | None:
    """Resolve a session cookie, sliding its expiry forward. None if it is not usable.

    Renewed on use rather than fixed: a rider who opens the crew panel every week should never
    see a sign-in screen again, while one who stops riding is signed out after ninety days
    without anybody having to run a cleanup.
    """
    if not sid:
        return None
    ws = db.get(WebSession, sid)
    if ws is None or ws.scope != scope:
        return None
    if (utcnow() - (ws.last_used or ws.created_at)) > SESSION_TTL:
        db.delete(ws)
        db.commit()
        return None
    rider = db.get(Rider, ws.store_id)
    if rider is None or rider.deleted_at is not None:
        db.delete(ws)                    # the account went away; so does the session
        db.commit()
        return None
    # Written at most hourly rather than on every call. This feeds a 90-day expiry, so a
    # timestamp good to the hour is good enough, and the alternative was a write plus a commit
    # on the one hot authenticated endpoint.
    now = utcnow()
    if ws.last_used is None or (now - ws.last_used) > timedelta(hours=1):
        ws.last_used = now
        db.commit()
    return ws


def revoke(db, sid: str) -> None:
    ws = db.get(WebSession, sid)
    if ws is not None:
        db.delete(ws)
        db.commit()


def revoke_all(db, store_id: str) -> int:
    """Every session for a rider — what a lost phone needs.

    The honest answer to "someone lost their phone": the store_id itself is restored by
    reinstalling the app on the same store account, because that is where it comes from. What
    this covers is the other half, signing out the sessions the old phone created. A rider who
    has lost the account itself needs an admin, which is deliberate — a self-service recovery
    path for a password-less identity is a self-service account-takeover path.
    """
    n = 0
    for ws in db.query(WebSession).filter(WebSession.store_id == store_id).all():
        db.delete(ws)
        n += 1
    db.commit()
    return n


def sweep(db) -> int:
    """Drop expired pairings and dead sessions. Cheap; called from the retention loop."""
    n = 0
    cutoff = utcnow() - CODE_TTL
    for pt in db.query(PairToken).filter(PairToken.created_at < cutoff).all():
        db.delete(pt)
        n += 1
    scutoff = utcnow() - SESSION_TTL
    for ws in db.query(WebSession).filter(WebSession.last_used < scutoff).all():
        db.delete(ws)
        n += 1
    db.commit()
    return n


def qr_png_b64(text: str, scale: int = 6) -> str:
    """A QR code as a base64 PNG, for embedding straight into the page.

    Inline rather than a second request: the code it carries lives three minutes, and a URL
    that serves a pairing QR is a URL that can be fetched by something other than the browser
    that was given it.
    """
    import qrcode
    img = qrcode.make(text, box_size=scale, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
