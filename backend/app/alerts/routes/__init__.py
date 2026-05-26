"""Alerts module HTTP surface — aggregates submodule routers.

Mounted into FastAPI via `app.include_router(alerts_routes.router)` in
`app.main`. Phase 8 lands the empty-list scaffold; phase 12 fills in
the rule engine, alert rows, and acknowledgement actions.
"""

from fastapi import APIRouter

from app.alerts.routes import alerts

router = APIRouter()
router.include_router(alerts.router)
