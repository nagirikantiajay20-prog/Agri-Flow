from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    bookings,
    crops,
    events,
    farm_visits,
    farmers,
    grain_sales,
    notifications,
    public,
    seed_purchases,
    seeds,
    transactions,
    uploads,
    warehouses,
)
from app.api.v1 import farmer as farmer_app

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(auth.users_router)
api_router.include_router(public.router)
api_router.include_router(farmers.router)
api_router.include_router(crops.router)
api_router.include_router(farm_visits.router)
api_router.include_router(seeds.router)
api_router.include_router(seed_purchases.router)
api_router.include_router(warehouses.router)
api_router.include_router(warehouses.slots_router)
api_router.include_router(bookings.router)
api_router.include_router(grain_sales.router)
api_router.include_router(transactions.router)
api_router.include_router(notifications.router)
api_router.include_router(events.router)
api_router.include_router(admin.router)
api_router.include_router(admin.managers_router)
api_router.include_router(admin.market_rates_router)
api_router.include_router(uploads.router)
api_router.include_router(farmer_app.router)
