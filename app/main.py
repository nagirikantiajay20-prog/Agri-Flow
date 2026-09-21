"""
AgriFlow Backend — FastAPI application entrypoint.

Wires: CORS, request-ID + rate-limit middleware, the standard error
envelope, health checks (Master Plan §49/§50), and the v1 API router.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.logging import configure_logging, get_logger
from app.middleware.error_handler import register_exception_handlers
from app.middleware.metrics import MetricsMiddleware, metrics_endpoint
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware

configure_logging(settings.ENVIRONMENT)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = settings.validate_for_production()
    if problems:
        if settings.ENVIRONMENT == "production":
            # Fail loudly at boot rather than serving traffic with a
            # dev-grade secret / wildcard CORS / localhost Redis in
            # production (gap-fix #8 in the backend remediation plan).
            raise RuntimeError(
                "Refusing to start in production with unsafe configuration:\n  - "
                + "\n  - ".join(problems)
            )
        for p in problems:
            logger.warning("config_warning", detail=p)
    logger.info("startup", environment=settings.ENVIRONMENT)
    yield
    await engine.dispose()
    logger.info("shutdown")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
    redoc_url=f"{settings.API_V1_PREFIX}/redoc",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
if settings.METRICS_ENABLED:
    app.add_middleware(MetricsMiddleware)

register_exception_handlers(app)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}


@app.get("/health/live", tags=["health"])
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready", tags=["health"])
async def health_ready():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.error("readiness_db_check_failed", error=str(exc))
        db_ok = False
    from app.services import push_service

    return {
        "status": "ready" if db_ok else "not_ready",
        "database": db_ok,
        "push": push_service.is_enabled(),
    }


if settings.METRICS_ENABLED:
    # NOTE: not app-level authenticated — Prometheus scrape targets are
    # conventionally protected at the network layer (firewalled to the
    # scraper / internal network), not with a bearer token. If this
    # deployment doesn't have that isolation, put this behind a reverse
    # proxy rule or set METRICS_ENABLED=false and scrape a sidecar instead.
    app.add_api_route("/metrics", metrics_endpoint, tags=["health"], include_in_schema=False)
