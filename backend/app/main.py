"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import ensure_initial_admin
from app.config import get_settings
from app.db import SessionLocal
from app import migrations as db_migrations
from app import models  # noqa: F401  (registers all tables on Base.metadata)
from app.routes import (
    auth,
    credentials,
    devices,
    images,
    jobs,
    panoramas,
    precheck_sets,
    reports,
    snapshots,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Apply any pending Alembic migrations on every startup. Idempotent — no-op
    # if the DB is already at HEAD. This is what preserves your data across schema
    # changes: instead of recreating tables, we evolve them.
    try:
        db_migrations.run()
    except Exception:  # noqa: BLE001
        log.exception("Migration run failed")

    db = SessionLocal()
    try:
        ensure_initial_admin(db)
    except Exception:  # noqa: BLE001
        log.exception("Failed to create initial admin")
    finally:
        db.close()
    yield


app = FastAPI(
    title="pan-fw-upgrader",
    description="Bulk upgrade orchestration for Palo Alto Networks firewalls",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(panoramas.router)
app.include_router(credentials.router)
app.include_router(images.router)
app.include_router(jobs.router)
app.include_router(reports.router)
app.include_router(snapshots.router)
app.include_router(snapshots.device_router)
app.include_router(precheck_sets.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
