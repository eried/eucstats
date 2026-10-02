"""Admin: Crews & Territory — settings, moderation and the rebuild.

Kept in its own module rather than appended to a 2,700-line admin.py. It borrows that module's
shell and auth so the page is indistinguishable from the rest of the console.

What an admin can do here that nobody else can: switch the whole mode off, force a rebuild,
rename or disband a crew that has named itself something unacceptable, hand a crew back to a
rider who lost access, and sign every browser out of a rider's account. That last one is the
answer to a lost phone — see the note on recovery in services/pairing.py.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Clan, ClanCell, ClanMember, Rider, Trip, WebSession, utcnow
from services import adminauth, crews, pairing, settings, territory

crews_admin_router = APIRouter(prefix="/admin/crews", tags=["admin"])


def _shell(inner: str, active: str = "/admin/crews"):
    from web.admin import _admin_shell
    return _admin_shell(inner, active)


def _guard(request: Request):
    from web.admin import _is_enrolled
    if not _is_enrolled() or not adminauth.is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return None


def _flash(msg: str = "", err: str = "") -> str:
    out = ""
    if msg:
        out += f'<div class="flash ok">{html.escape(msg)}</div>'
    if err:
        out += f'<div class="flash err">{html.escape(err)}</div>'
    return out


def _swatch(colour: str, pattern: str) -> str:
    """The crew's identity drawn the way the map draws it, so the admin table and the ground
    agree at a glance."""
    pat = {
        "stripes": "repeating-linear-gradient(45deg,rgba(0,0,0,.45) 0 3px,transparent 3px 7px)",
        "hatch": ("repeating-linear-gradient(45deg,rgba(0,0,0,.42) 0 2px,transparent 2px 6px),"
                  "repeating-linear-gradient(-45deg,rgba(0,0,0,.42) 0 2px,transparent 2px 6px)"),
        "dots": "radial-gradient(rgba(0,0,0,.5) 28%,transparent 30%)",
    }.get(pattern, "none")
    size = "background-size:6px 6px;" if pattern == "dots" else ""
    return (f'<span style="display:inline-block;width:18px;height:18px;vertical-align:middle;'
            f'border:1px solid rgba(255,255,255,.3);background-color:{html.escape(colour)};'
            f'background-image:{pat};{size}"></span>')


def _page(db: Session, msg: str = "", err: str = "") -> str:
    cfg = settings.get_crews(db)
    rank = {r["clan_id"]: r for r in territory.ranking(db, limit=1000)}
    all_crews = (db.query(Clan).order_by(Clan.created_at.desc()).all())
    bound = adminauth.bound_store_id()
    bound_name = "—"
    if bound:
        r = db.get(Rider, bound)
        bound_name = html.escape(r.display_name) if r else bound[:12] + "…"

    on = " checked" if cfg["enabled"] else ""
    open_on = " checked" if cfg["creation_open"] else ""
    head = f"""
    <div class=card>
      <h1>Crews &amp; Territory</h1>
      <p class=hint>Riders group into crews and claim ground by riding it. The mode is off
      until you switch it on, and switching it off hides it everywhere without deleting a
      single row — crews, members and held tiles all survive.</p>
      {_flash(msg, err)}
      <form method=post action="/admin/crews/settings">
        <table class=form>
          <tr><td><label><input type=checkbox name=enabled{on}> <b>Crews mode is live</b></label></td>
              <td class=mut>Off: the dock button is gone and every crew endpoint 404s.</td></tr>
          <tr><td><label><input type=checkbox name=creation_open{open_on}> Anyone may found a crew</label></td>
              <td class=mut>Off: existing crews carry on, no new ones are created.</td></tr>
          <tr><td>Tile zoom <input name=zoom value="{cfg['zoom']}" size=4></td>
              <td class=mut>13 is about 2.4&nbsp;km across at Oslo and 4.9&nbsp;km at the
              equator. Lower is coarser. Changing this invalidates every held tile.</td></tr>
          <tr><td>Rolling window (days) <input name=window_days value="{cfg['window_days']}" size=5></td>
              <td class=mut>Only rides inside the window count, so a crew that stops riding
              fades instead of holding ground forever.</td></tr>
          <tr><td>Seed block <input name=seed value="{cfg['seed']}" size=4></td>
              <td class=mut>How many tiles square a crew must hold before it holds anything.
              2 means a 2&times;2 block. Set 1 to let single tiles count, which makes the map
              much noisier.</td></tr>
          <tr><td>Leave cooldown (days) <input name=cooldown_days value="{cfg['cooldown_days']}" size=4></td>
              <td class=mut>How long after leaving before a rider may join another crew.</td></tr>
          <tr><td>Max members <input name=max_members value="{cfg['max_members']}" size=5></td>
              <td class=mut>0 = no cap.</td></tr>
          <tr><td>Fill opacity <input name=opacity value="{cfg['opacity']}" size=5></td>
              <td class=mut>How strongly the rectangles paint over the map.</td></tr>
        </table>
        <button>Save</button>
      </form>
    </div>

    <div class=card>
      <h2>Admin sign-in</h2>
      <p class=hint>This console now wants two factors: the authenticator code, and a pairing
      confirmed from a phone. The pairing proves possession; the code proves knowledge. A
      rider id on its own is <b>never</b> enough — it travels in every upload, so it is a
      username, not a password.</p>
      <p>Bound to: <b>{bound_name}</b>
      {'<span class=mut>&nbsp;· the first phone to pair claimed it</span>' if bound else
       '<span class=mut>&nbsp;· nothing has paired yet; the first one to do so claims it</span>'}</p>
      <p class=hint>Lost that phone? There is no button here for it on purpose. Set
      <code>"admin_require_pairing": false</code> in <code>data/admin.json</code> over SSH,
      sign in with the code alone, pair the new phone, and set it back. A console that can be
      downgraded to one factor from inside the console has one factor.</p>
    </div>

    <div class=card>
      <h2>Territory</h2>
      <p class=hint>Rebuilt hourly by the background loop. Force one here after changing the
      zoom, the window or the seed block — none of those take effect on the map until the
      tiles are recomputed.</p>
      <form method=post action="/admin/crews/rebuild"><button>Rebuild now</button></form>
    </div>
    """

    rows = ""
    for c in all_crews:
        n = (db.query(ClanMember)
             .filter(ClanMember.clan_id == c.clan_id, ClanMember.status == "active",
                     ClanMember.left_at.is_(None)).count())
        trips = db.query(Trip).filter(Trip.clan_id == c.clan_id).count()
        held = rank.get(c.clan_id)
        dead = c.disbanded_at is not None
        leader = (db.query(ClanMember)
                  .filter(ClanMember.clan_id == c.clan_id, ClanMember.role == "leader",
                          ClanMember.left_at.is_(None)).first())
        lname = "—"
        if leader:
            lr = db.get(Rider, leader.store_id)
            lname = html.escape(lr.display_name) if lr else leader.store_id[:10]
        rows += (
            f'<tr{" class=mut" if dead else ""}>'
            f'<td>{_swatch(c.colour, c.pattern)} <b>{html.escape(c.name)}</b>'
            f'{" <span class=mut>· disbanded</span>" if dead else ""}'
            f'<div class=mut style="font-size:11px">{html.escape(c.description or "")}</div></td>'
            f'<td>{html.escape(c.pattern)}</td>'
            f'<td>{lname}</td><td>{n}</td><td>{trips}</td>'
            f'<td>{held["km2"] if held else 0} km²<div class=mut style="font-size:11px">'
            f'{held["tiles"] if held else 0} tiles</div></td>'
            f'<td>{html.escape(c.join_policy)}</td>'
            f'<td><form method=post action="/admin/crews/rename" style="display:flex;gap:4px">'
            f'<input type=hidden name=clan_id value="{c.clan_id}">'
            f'<input name=name placeholder="new name" size=14>'
            f'<button class="ghost mini">rename</button></form></td>'
            f'<td>' + (
                f'<form method=post action="/admin/crews/restore">'
                f'<input type=hidden name=clan_id value="{c.clan_id}">'
                f'<button class="ghost mini">restore</button></form>' if dead else
                f'<form method=post action="/admin/crews/disband">'
                f'<input type=hidden name=clan_id value="{c.clan_id}">'
                f'<input name=confirm placeholder="type name" size=12>'
                f'<button class=danger>disband</button></form>')
            + '</td></tr>')

    table = f"""
    <div class=card>
      <h2>Crews <span class=mut>({len(all_crews)})</span></h2>
      <p class=hint>Renaming keeps the crew's link working — the slug is not regenerated.
      Disbanding is reversible: the crew is marked, not deleted, because its rides still point
      at it and deleting it would tear months of territory out of the map. Its colour and
      pattern are freed immediately for somebody else.</p>
      <table class=grid>
      <tr><th>Crew</th><th>Pattern</th><th>Leader</th><th>Members</th><th>Rides</th>
          <th>Held</th><th>Joining</th><th>Rename</th><th></th></tr>
      {rows or '<tr><td colspan=9 class=mut>No crews yet.</td></tr>'}
      </table>
    </div>
    """

    sessions = db.query(WebSession).count()
    sess = f"""
    <div class=card>
      <h2>Sessions</h2>
      <p class=hint>{sessions} browser session{"s" if sessions != 1 else ""}
      {"is" if sessions == 1 else "are"} signed in through a paired phone. A crew session can create, join and leave crews and act on
      members — it cannot upload a ride, rename a rider, or delete anything. Signing a rider
      out is what a lost phone needs; the rider id itself comes back when they reinstall the
      app on the same store account.</p>
      <form method=post action="/admin/crews/revoke" style="display:flex;gap:6px">
        <input name=store_id placeholder="store_id" size=30>
        <button class="ghost">Sign out every browser for this rider</button>
      </form>
    </div>
    """
    return _shell(head + table + sess)


@crews_admin_router.get("", response_class=HTMLResponse)
def page(request: Request, db: Session = Depends(get_db), msg: str = "", err: str = ""):
    g = _guard(request)
    if g:
        return g
    return HTMLResponse(_page(db, msg, err))


def _redir(msg: str = "", err: str = ""):
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    return RedirectResponse("/admin/crews" + ("?" + q if q else ""), status_code=303)


@crews_admin_router.post("/settings")
def save_settings(request: Request, db: Session = Depends(get_db),
                  enabled: str = Form(None), creation_open: str = Form(None),
                  zoom: int = Form(13), window_days: int = Form(90), seed: int = Form(2),
                  cooldown_days: int = Form(7), max_members: int = Form(0),
                  opacity: float = Form(0.55)):
    g = _guard(request)
    if g:
        return g
    before = settings.get_crews(db)
    settings.set_crews(db, bool(enabled), zoom, window_days, seed, cooldown_days,
                       max_members, opacity, bool(creation_open))
    after = settings.get_crews(db)
    crews.COOLDOWN_DAYS = after["cooldown_days"]
    note = "Saved."
    if (before["zoom"], before["window_days"], before["seed"]) != \
       (after["zoom"], after["window_days"], after["seed"]):
        note += " The grid changed — rebuild territory for it to show on the map."
    return _redir(note)


@crews_admin_router.post("/rebuild")
def rebuild(request: Request, db: Session = Depends(get_db)):
    g = _guard(request)
    if g:
        return g
    cfg = settings.get_crews(db)
    rep = territory.rebuild(db, window_days=cfg["window_days"], zoom=cfg["zoom"])
    pairing.sweep(db)
    return _redir(f"Rebuilt: {rep['held']} tiles held by {rep['crews']} crews "
                  f"across {rep['regions']} regions ({rep['bytes']} bytes on the wire).")


@crews_admin_router.post("/rename")
def rename(request: Request, db: Session = Depends(get_db),
           clan_id: str = Form(...), name: str = Form("")):
    g = _guard(request)
    if g:
        return g
    c = db.get(Clan, clan_id)
    if c is None:
        return _redir(err="No such crew.")
    name = (name or "").strip()
    if not crews.NAME_RE.match(name):
        return _redir(err="A name is 3-28 characters.")
    if db.query(Clan).filter(Clan.name == name, Clan.clan_id != clan_id,
                             Clan.disbanded_at.is_(None)).first():
        return _redir(err="That name is taken.")
    old = c.name
    c.name = name
    db.commit()                 # the slug is deliberately left alone: links should not rot
    return _redir(f"Renamed {old} to {name}.")


@crews_admin_router.post("/disband")
def disband(request: Request, db: Session = Depends(get_db),
            clan_id: str = Form(...), confirm: str = Form("")):
    g = _guard(request)
    if g:
        return g
    c = db.get(Clan, clan_id)
    if c is None:
        return _redir(err="No such crew.")
    if (confirm or "").strip() != c.name:
        return _redir(err="Type the crew's name exactly to disband it.")
    c.disbanded_at = utcnow()
    for m in db.query(ClanMember).filter(ClanMember.clan_id == clan_id,
                                         ClanMember.left_at.is_(None)).all():
        m.left_at = utcnow()
    db.query(ClanCell).filter(ClanCell.clan_id == clan_id).delete()
    db.commit()
    return _redir(f"{c.name} disbanded. Its rides keep pointing at it; its colour is free.")


@crews_admin_router.post("/restore")
def restore(request: Request, db: Session = Depends(get_db), clan_id: str = Form(...)):
    """Undo a disband. Possible precisely because disbanding only marks the crew."""
    g = _guard(request)
    if g:
        return g
    c = db.get(Clan, clan_id)
    if c is None:
        return _redir(err="No such crew.")
    if crews.identity_taken(db, c.colour, c.pattern, exclude=c.clan_id):
        return _redir(err=f"Another crew took {c.colour}/{c.pattern} while it was disbanded. "
                          "Give that crew different colours first.")
    c.disbanded_at = None
    db.commit()
    return _redir(f"{c.name} restored. Rebuild territory to bring its ground back.")


@crews_admin_router.post("/revoke")
def revoke(request: Request, db: Session = Depends(get_db), store_id: str = Form("")):
    g = _guard(request)
    if g:
        return g
    n = pairing.revoke_all(db, (store_id or "").strip())
    return _redir(f"Signed out {n} browser session{'s' if n != 1 else ''}.")
