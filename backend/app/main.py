"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import Base, engine
# Importing the models package registers every table on Base.metadata.
from app import models  # noqa: F401
from app.routes import credentials, devices, metrics, panoramas
from app.services import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables if they don't exist. Switch to Alembic
    # before this app sees real upgrades-in-place.
    Base.metadata.create_all(bind=engine)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


settings = get_settings()
app = FastAPI(title="pan-capacity-analyzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(credentials.router)
app.include_router(panoramas.router)
app.include_router(devices.router)
app.include_router(metrics.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
