"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi import Depends

from app.capacity.routes import metrics
from app.capacity.services import scheduler
from app.config import get_settings
from app.core.auth.deps import current_user
from app.core.auth.routes import auth, providers, users
from app.core.devices.routes import devices
from app.core.migrations import run_migrations
from app.core.panorama.routes import panoramas

# Importing each module's models package registers every table on
# Base.metadata so alembic's autogenerate sees them.
from app.capacity import models as _capacity_models  # noqa: F401
from app.core.auth import models as _auth_models  # noqa: F401
from app.core.devices import models as _devices_models  # noqa: F401
from app.core.panorama import models as _panorama_models  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run schema migrations on every startup. Idempotent — no-op at head.
    # Has built-in guards for: empty migration stubs (§3.2 trap) and the
    # first-run-against-legacy-create_all-schema case (stamps instead of
    # re-creating). See app/core/migrations.py.
    run_migrations()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


settings = get_settings()
app = FastAPI(title="pan-ops-console", lifespan=lifespan)

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
app.include_router(providers.router, dependencies=_auth_required)


@app.get("/")
def root():
    return {
        "name": "pan-ops-console",
        "docs": "/docs",
        "healthz": "/healthz",
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
