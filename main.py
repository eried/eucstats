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
            db = SessionLocal()
            try:
                from services import health
                n = run_retention(db)
                if n:
                    logger.info("retention evicted %d raw uploads", n)
                health.heartbeat(db)             # periodic health snapshot -> data/health.log
            finally:
                db.close()
        except Exception:
            logger.exception("retention run failed")


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
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="eucstats", lifespan=lifespan)

from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
from web.api import router as api_router  # noqa: E402
from web.admin import admin_router, _get_session_secret  # noqa: E402
from web.public import public_router  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.add_middleware(SessionMiddleware, secret_key=_get_session_secret())
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "web" / "static")), name="static")
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(public_router)


@app.get("/health")
def health():
    return {"ok": True}
