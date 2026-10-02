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
            # territory rebuild grows with the number of trips: measured, it blocked the loop
            # for 1,487 ms at today's size and 17 seconds at a hundred times the trips, which
            # on one worker is the whole site down, once an hour, for as long as it takes.
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
        if _time.time() - _last_territory[0] < 3600:
            return
        from services import territory
        rep = territory.rebuild(db, window_days=cfg["window_days"], zoom=cfg["zoom"],
                               seed=cfg["seed"])
        _last_territory[0] = _time.time()
        logger.info("territory rebuilt: %s", rep)
    except Exception:
        logger.exception("territory rebuild failed")


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
    try:
        yield
    finally:
        task.cancel()
        tg_task.cancel()


app = FastAPI(title="eucstats", lifespan=lifespan)

from starlette.middleware.gzip import GZipMiddleware  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
from web.api import router as api_router  # noqa: E402
from web.crews_api import router as crews_router, pair_router  # noqa: E402
from web.admin import admin_router, _get_session_secret  # noqa: E402
from web.admin_crews import crews_admin_router  # noqa: E402
from web.public import public_router  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

# The page is one 132 KB inline HTML string and was going out uncompressed whenever nginx
# was not in front of it; crews adds 56 KB of static on top. minimum_size keeps it off the
# small JSON responses, where the CPU is not worth the few bytes. The territory payload is
# already gzipped on disk and sets its own Content-Encoding, so this leaves it alone.
app.add_middleware(GZipMiddleware, minimum_size=1024)
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
