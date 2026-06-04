"""Public + ingest API (/api/v1)."""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

import config
from database import get_db
from services import stats
from services.identity import ChangeNotAllowed, IdentityService
from services.ingest import IngestError, IngestService

router = APIRouter(prefix="/api/v1", tags=["api"])


# --- riders / profile ---

@router.post("/riders")
def register_rider(payload: dict, db: Session = Depends(get_db)):
    store = payload.get("store_id")
    if not store or not payload.get("display_name"):
        raise HTTPException(400, "store_id and display_name are required")
    avatar = None
    if payload.get("avatar_png_base64"):
        try:
            avatar = base64.b64decode(payload["avatar_png_base64"])
        except Exception:
            raise HTTPException(400, "avatar_png_base64 is not valid base64")
    svc = IdentityService(db)
    svc.register(store, payload.get("platform", "google_play"), payload["display_name"],
                 payload.get("flag"), avatar, bool(payload.get("consent_public", True)))
    return svc.get_profile(store)


@router.get("/riders/{store_id}")
def get_rider(store_id: str, db: Session = Depends(get_db)):
    p = IdentityService(db).get_profile(store_id)
    if p is None:
        raise HTTPException(404, "rider not found")
    return p


@router.patch("/riders/{store_id}")
def patch_rider(store_id: str, payload: dict, db: Session = Depends(get_db)):
    svc = IdentityService(db)
    changes = []
    if "display_name" in payload:
        changes.append(("name", payload["display_name"]))
    if "flag" in payload:
        changes.append(("flag", payload["flag"]))
    if "avatar_png_base64" in payload:
        try:
            changes.append(("avatar", base64.b64decode(payload["avatar_png_base64"])))
        except Exception:
            raise HTTPException(400, "avatar_png_base64 is not valid base64")
    try:
        for field, value in changes:
            svc.update(store_id, field, value)
    except ChangeNotAllowed as e:
        raise HTTPException(429, str(e))
    except KeyError:
        raise HTTPException(404, "rider not found")
    p = svc.get_profile(store_id)
    if p is None:
        raise HTTPException(404, "rider not found")
    return p


@router.delete("/riders/{store_id}")
def delete_rider(store_id: str, db: Session = Depends(get_db)):
    IdentityService(db).delete(store_id)
    return {"deleted": store_id}


@router.get("/riders/{store_id}/export")
def export_rider(store_id: str, db: Session = Depends(get_db)):
    p = IdentityService(db).export(store_id)
    if p is None:
        raise HTTPException(404, "rider not found")
    return p


@router.get("/riders/{store_id}/avatar")
def get_avatar(store_id: str, db: Session = Depends(get_db)):
    r = IdentityService(db).repo.get(store_id)
    if not r or not r.avatar_png:
        raise HTTPException(404, "no avatar")
    return Response(content=r.avatar_png, media_type="image/png")


# --- public leaderboards / map / records ---

@router.get("/leaderboards/{board}")
def leaderboard(board: str, limit: int = 50, db: Session = Depends(get_db)):
    fn = stats.BOARDS.get(board)
    if fn is None:
        raise HTTPException(404, f"unknown board: {board}")
    return {"board": board, "entries": fn(db, min(limit, 200))}


@router.get("/countries")
def list_countries(db: Session = Depends(get_db)):
    return stats.countries(db)


@router.get("/records")
def list_records(db: Session = Depends(get_db)):
    return stats.records(db)


@router.get("/map/cells")
def map_cells(zoom: float = 0.1, db: Session = Depends(get_db)):
    return stats.map_cells(db, zoom)


@router.get("/stats/summary")
def stats_summary(db: Session = Depends(get_db)):
    return stats.global_summary(db)


@router.get("/stats/versions")
def stats_versions(db: Session = Depends(get_db)):
    return stats.version_stats(db)


@router.get("/groups/{kind}")
def groups(kind: str, db: Session = Depends(get_db)):
    fns = {"brand": stats.by_brand, "wheel": stats.by_wheel, "country": stats.by_country}
    fn = fns.get(kind)
    if fn is None:
        raise HTTPException(404, f"unknown group: {kind}")
    return {"kind": kind, "entries": fn(db, 50)}


@router.get("/champions")
def all_champions(db: Session = Depends(get_db)):
    return stats.champions(db)


@router.get("/champions/weekly")
def weekly_champion(db: Session = Depends(get_db)):
    from models import LeaderboardSnapshot
    snap = (db.query(LeaderboardSnapshot)
            .filter_by(period_type="week", board="distance")
            .order_by(LeaderboardSnapshot.period_key.desc()).first())
    return snap.payload if snap else {"champion": None, "top": []}


# --- trip ingest ---

@router.post("/trips")
async def upload_trip(meta: str = Form(...), trip: UploadFile = File(...),
                      db: Session = Depends(get_db)):
    try:
        meta_obj = json.loads(meta)
    except Exception:
        raise HTTPException(400, "meta is not valid JSON")
    raw = await trip.read()
    if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, "trip file too large")
    try:
        result = IngestService(db).handle(meta_obj, raw)
    except IngestError as e:
        raise HTTPException(e.code, e.detail)
    return JSONResponse(result, status_code=200 if result.get("duplicate") else 201)
