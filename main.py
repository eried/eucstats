"""eucstats FastAPI application entrypoint (served as `gunicorn main:app`)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="eucstats", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    return {"service": "eucstats", "status": "ok"}
