"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi import Depends

from app.config import get_settings
from app.db import Base, engine
# Importing the models package registers every table on Base.metadata.
from app import models  # noqa: F401
from app.routes import auth, devices, metrics, panoramas, users
from app.services import scheduler
from app.services.auth_dep import current_user

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

# Public routes — no auth required.
app.include_router(auth.router)

# Protected routes — every endpoint in these routers requires a valid
# session. Applied at the include-router level so individual handlers
# don't have to remember the dependency, and so adding a new route can
# never accidentally land unauthenticated.
_auth_required = [Depends(current_user)]
app.include_router(panoramas.router, dependencies=_auth_required)
app.include_router(devices.router, dependencies=_auth_required)
app.include_router(metrics.router, dependencies=_auth_required)
app.include_router(users.router, dependencies=_auth_required)


@app.get("/")
def root():
    return {
        "name": "pan-capacity-analyzer",
        "docs": "/docs",
        "healthz": "/healthz",
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
