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
        await asyncio.sleep(config.RETENTION_INTERVAL_S)
        try:
            db = SessionLocal()
            try:
                n = run_retention(db)
                if n:
                    logger.info("retention evicted %d raw uploads", n)
            finally:
                db.close()
        except Exception:
            logger.exception("retention run failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="eucstats", lifespan=lifespan)

from web.api import router as api_router  # noqa: E402

app.include_router(api_router)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    return {"service": "eucstats", "status": "ok"}
