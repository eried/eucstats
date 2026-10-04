"""eucstats FastAPI application entrypoint (served as `gunicorn main:app`)."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
from database import SessionLocal, init_db
from services.retention import run_retention

logger = logging.getLogger("eucstats")


async def _retention_loop():
    while True:
        interval = config.RETENTION_INTERVAL_S
        try:
            db = SessionLocal()
            try:
                from services.settings import get_retention
                interval = get_retention(db)["interval_s"]   # admin-tunable cadence
            finally:
                db.close()
        except Exception:
            pass
        await asyncio.sleep(interval)
        try:
            # On a thread, not on the event loop. All three of these are synchronous and the
            # territory rebuild grows with the number of trips: measured, 182 ms at today's
            # size and about half a minute at a hundred times the trips, which on one worker
            # would be the whole site down, once an hour, for as long as it takes. The 1,487 ms
            # this comment used to claim was a profiler's own overhead, not the rebuild.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _retention_once)
        except Exception:
            logger.exception("retention run failed")


def _retention_once() -> None:
    """One pass of the periodic housekeeping. Synchronous on purpose; the caller gives it a
    thread."""
    db = SessionLocal()
    try:
        from services import health, pairing
        n = run_retention(db)
        if n:
            logger.info("retention evicted %d raw uploads", n)
        _territory_if_due(db)
        # swept here rather than inside the territory job, which returns early when crews are
        # switched off and only runs hourly: expired pairings are this loop's business
        try:
            pairing.sweep(db)
        except Exception:
            logger.exception("pairing sweep failed")
        health.heartbeat(db)                     # periodic health snapshot -> data/health.log
    finally:
        db.close()


_last_territory = [0.0]


def _territory_if_due(db) -> None:
    """Rebuild crew territory at most once an hour, and only while crews are switched on.

    Shares the retention loop instead of adding a scheduler. Territory is derived data: a
    rebuild that fails or is skipped costs a slightly stale map and nothing else, so it is
    deliberately the lowest-priority job on the box.
    """
    import time as _time
    from services import settings as _settings
    try:
        cfg = _settings.get_crews(db)
        if not cfg["enabled"]:
            return
        from web.crews_api import REBUILD_EVERY_S
        if _time.time() - _last_territory[0] < REBUILD_EVERY_S:
            return
        from services import territory
        rep = territory.rebuild(db, window_days=cfg["window_days"], zoom=cfg["zoom"],
                               seed=cfg["seed"])
        _last_territory[0] = _time.time()
        logger.info("territory rebuilt: %s", rep)
    except Exception:
        logger.exception("territory rebuild failed")


async def _territory_fresh_loop():
    """Redraw the map shortly after a ride, rather than on the hour.

    The rebuild rides the retention loop, which is hourly by default, so a rider's own ride
    reached the map between a second and sixty minutes after they uploaded it. "You ride the
    block, come home, open the panel, and the squares have not moved" was the oldest item on
    the open list and the one thing a real ride test is guaranteed to hit.

    This checks a flag, which costs nothing, and only rebuilds when a ride that could move a
    square has actually landed. `FRESH_GAP_S` is the floor between two such rebuilds, so a
    group ride finishing together is one redraw and not one per rider. The hourly pass stays
    exactly as it was, underneath this, for everything else that makes the map stale -- a crew
    folding, ground going cold, an admin changing the zoom.
    """
    from services import territory
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(15)
        try:
            if not territory.is_dirty():
                continue
            import time as _time
            if _time.time() - _last_territory[0] < territory.FRESH_GAP_S:
                continue
            await loop.run_in_executor(None, _territory_fresh_once)
        except Exception:
            logger.exception("territory fresh rebuild failed")


def _territory_fresh_once() -> None:
    """One post-ride rebuild, on a thread. Synchronous on purpose, like `_retention_once`."""
    db = SessionLocal()
    try:
        from services import settings as _settings, territory
        cfg = _settings.get_crews(db)
        if not cfg["enabled"]:
            territory.claim_dirty()        # nothing to draw; do not spin on the mark
            return
        # Claimed before the work, not after: a ride arriving DURING the rebuild has to leave
        # the mark set so the next pass picks it up, or it is the one ride that never lands.
        if not territory.claim_dirty():
            return
        import time as _time
        rep = territory.rebuild(db, window_days=cfg["window_days"], zoom=cfg["zoom"],
                                seed=cfg["seed"])
        _last_territory[0] = _time.time()
        logger.info("territory rebuilt after a ride: %s", rep)
    finally:
        db.close()


async def _telegram_daily_loop():
    """Post the daily Telegram summary once per day at the configured local time. Best-effort:
    the send is gated + idempotent (persists last_summary_date), and errors never stop the loop."""
    from services import telegram
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(300)                 # check every 5 min; run_daily_if_due decides if it's due
        try:
            await loop.run_in_executor(None, telegram.run_daily_if_due)
        except Exception:
            logger.exception("telegram daily summary check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:                                         # one health snapshot at startup
        from services import health
        db = SessionLocal()
        try:
            health.heartbeat(db)
        finally:
            db.close()
    except Exception:
        pass
    task = asyncio.create_task(_retention_loop())
    tg_task = asyncio.create_task(_telegram_daily_loop())
    # Redraws the map shortly after a ride instead of on the hour; see the loop's own note.
    fresh_task = asyncio.create_task(_territory_fresh_loop())
    try:
        yield
    finally:
        task.cancel()
        tg_task.cancel()
        fresh_task.cancel()


app = FastAPI(title="eucstats", lifespan=lifespan)

from starlette.middleware.gzip import GZipMiddleware  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
from web.api import router as api_router  # noqa: E402
from web.crews_api import router as crews_router, pair_router  # noqa: E402
from web.admin import admin_router, _get_session_secret  # noqa: E402
from web.admin_crews import crews_admin_router  # noqa: E402
from web.public import public_router  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

# Already-compressed files are sent as they are. GZipMiddleware decides on size alone, so it
# was re-compressing video and PNGs: intro.mp4 cost 50 ms of CPU to save 1,583 bytes out of a
# megabyte (six times the work of just sending it), and favicon.png came out 23 bytes BIGGER.
# On a single worker whose page ceiling is about 57 requests a second, that is the landing
# page paying for nothing. It also removes the invalid combination the measurement turned up:
# a 206 range response carrying Content-Encoding, where the byte range is stated over the
# uncompressed representation and the body is compressed.
_PRECOMPRESSED = (".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                  ".avif", ".ico", ".woff", ".woff2", ".zip", ".gz", ".mp3", ".ogg")


class SelectiveGZip(GZipMiddleware):
    """GZip, except for bytes that are already compressed."""

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "").lower()
            if path.endswith(_PRECOMPRESSED):
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


app.add_middleware(SelectiveGZip, minimum_size=1024)
app.add_middleware(SessionMiddleware, secret_key=_get_session_secret())
class _CachedStatic(StaticFiles):
    """Static files with a cache lifetime.

    They shipped an ETag and a Last-Modified but no Cache-Control, so every navigation paid a
    conditional request for every asset. A week, and the filenames are stable, so a changed
    file is picked up by the ETag revalidation after it.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers.setdefault("Cache-Control", "public, max-age=604800")
        return resp


app.mount("/static", _CachedStatic(directory=str(config.BASE_DIR / "web" / "static")),
          name="static")
app.include_router(api_router)
app.include_router(crews_router)
app.include_router(pair_router)
app.include_router(crews_admin_router)   # before admin_router: more specific prefix
app.include_router(admin_router)
app.include_router(public_router)


@app.get("/health")
def health():
    return {"ok": True}
