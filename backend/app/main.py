"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi import Depends

from app.alerts.routes import router as alerts_router
from app.capacity.routes import aggregates as capacity_aggregates
from app.capacity.routes import metrics
from app.config import get_settings
from app.core.auth.deps import current_user
from app.core.auth.routes import auth, providers, users
from app.core.devices.routes import devices
from app.core.migrations import run_migrations
from app.core.panorama.routes import panoramas
from app.upgrade.routes import router as upgrade_router

# Importing each module's models package registers every table on
# Base.metadata so alembic's autogenerate sees them.
from app.alerts import models as _alerts_models  # noqa: F401
from app.capacity import models as _capacity_models  # noqa: F401
from app.core.auth import models as _auth_models  # noqa: F401
from app.core.devices import models as _devices_models  # noqa: F401
from app.core.panorama import models as _panorama_models  # noqa: F401
from app.upgrade import models as _upgrade_models  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run schema migrations on every startup. Idempotent — no-op at head.
    # Has built-in guards for: empty migration stubs (§3.2 trap) and the
    # first-run-against-legacy-create_all-schema case (stamps instead of
    # re-creating). See app/core/migrations.py.
    run_migrations()
    # Capacity polling no longer runs in-process. The `capacity.poll_all`
    # Celery task (app/capacity/tasks/__init__.py) is driven by the beat
    # process on the configured POLL_INTERVAL_SECONDS schedule. The
    # in-process APScheduler that lived here through phases 1–4 was
    # retired at phase 2e cutover.
    yield


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
# Upgrade module — phase 4c-routes. Job + image management endpoints
# under /upgrade/*. The Celery dispatch wiring (phase 4d) is intentionally
# missing from `start_job` — see the TODO in that route. Endpoints work
# today for create / list / observe / abort / delete + per-task confirm /
# override / retry; orchestration runs once 4d lands.
app.include_router(upgrade_router, dependencies=_auth_required)
# Capacity-analyzer aggregation endpoints — phase 8. Powers the heat-map
# (phase 9), table (phase 10), and trend (phase 11) views with read-only
# aggregates over the existing samples + devices tables. No schema added.
app.include_router(capacity_aggregates.router, dependencies=_auth_required)
# Alerts module — phase 8 scaffold (empty list). Phase 12 fills in the
# real rule engine + acknowledgement actions. Mounting the route now
# means the phase-7 Home Dashboard's Active-alerts frame can fetch
# from /alerts immediately and just renders the empty state.
app.include_router(alerts_router, dependencies=_auth_required)


@app.get("/")
async def root():
    return {
        "name": "pan-ops-console",
        "docs": "/docs",
        "healthz": "/healthz",
    }


@app.get("/healthz")
async def healthz():
    """Kubernetes liveness/readiness probe target.

    `async def` is load-bearing here. A sync `def` endpoint in FastAPI
    runs in the default starlette thread pool — the SAME pool that
    serves every other sync route (notably `/metrics/{device_id}/{metric}`,
    which is fired ~17× in parallel on every Dashboard page load).
    Under that load the probe can wait seconds for a worker thread,
    causing intermittent timeoutSeconds breaches even when the app
    itself is fine.

    Because the body is trivial (no I/O, no DB), `async def` runs it
    inline on the event loop — no thread contention. Probe latency
    becomes effectively constant regardless of how many sync routes
    are concurrently busy.

    Companion to homelab#75 (which bumped probe timeoutSeconds from
    3s to 5s as a stopgap while the root cause was investigated).
    """
    return {"status": "ok"}
