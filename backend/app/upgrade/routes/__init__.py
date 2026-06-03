"""Upgrade module's HTTP surface — aggregates submodule routers.

Mounted into FastAPI via `app.include_router(upgrade_routes.router)` in
`app.main`. The submodule split (jobs.py + images.py) is by surface,
not by functional layer — every file here is "route + dependency
wiring," with logic in `app.upgrade.services`.

Adding a new sub-surface (e.g. precheck.py for standalone precheck
routes, snapshot.py for ad-hoc snapshot capture): create the file with
its own `APIRouter()`, import it here, and `router.include_router(...)`.
"""

from fastapi import APIRouter

from app.upgrade.routes import (
    disk,
    images,
    jobs,
    precheck_sets,
    snapshots,
    software,
)

router = APIRouter()
router.include_router(jobs.router)
router.include_router(images.router)
router.include_router(precheck_sets.router)
router.include_router(snapshots.router)
router.include_router(software.router)
router.include_router(disk.router)
