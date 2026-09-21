"""
Farmer mobile app API — /api/v1/farmer/*.

A thin, mobile-shaped layer over the same services the web app uses, so
seed stock locking, slot capacity locking, state machines, notifications
and audit are enforced identically for both clients. Every route is
farmer-only (app.core.dependencies.require_farmer) and derives the farmer
from the access token — no route accepts a farmer_id from the client.
"""
from fastapi import APIRouter

from app.api.v1.farmer import (
    auth,
    crops,
    dashboard,
    grain_sales,
    ledger,
    notifications,
    profile,
    seeds,
    warehouses,
)

router = APIRouter(prefix="/farmer")

for module in (auth, profile, dashboard, seeds, crops, warehouses, grain_sales, notifications, ledger):
    router.include_router(module.router)
