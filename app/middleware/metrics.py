"""
Basic Prometheus-style metrics (gap-fix #9 in the backend remediation
plan). Deliberately minimal — request counts/latencies by route+status,
plus a couple of business-relevant counters for the concurrency-critical
paths — not a full observability stack. Master Plan §49 asks for
"structured logs, request IDs, error tracking, metrics, health checks";
structured logs + request IDs + health checks already existed
(app.core.logging, app.middleware.request_id, app.main health routes).
This fills in "metrics".

Deliberately does NOT track anything from §49's "never expose" list
(password, JWT, OTP, bank account, secret keys) — only route template,
method, status code, and latency are recorded, never request/response
bodies or headers.
"""
import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "agriflow_http_requests_total",
    "Total HTTP requests",
    ["method", "path_template", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "agriflow_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path_template"],
)

# Business-relevant counters for the two concurrency-critical flows
# (Master Plan §39) — cheap to watch on a dashboard for oversell alarms
# without grepping logs.
BOOKING_CAPACITY_REJECTIONS = Counter(
    "agriflow_booking_capacity_rejections_total",
    "Booking requests rejected because the warehouse slot had no remaining capacity",
)
SEED_STOCK_REJECTIONS = Counter(
    "agriflow_seed_stock_rejections_total",
    "Seed purchase requests rejected due to insufficient stock",
)


def _path_template(request: Request) -> str:
    # Prefer the matched route's path template (e.g. "/bookings/{booking_id}/status")
    # over the raw URL, so metrics don't explode into one series per UUID.
    route = request.scope.get("route")
    if route is not None and hasattr(route, "path"):
        return route.path
    return request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        path_template = _path_template(request)
        REQUEST_COUNT.labels(
            method=request.method, path_template=path_template, status_code=response.status_code
        ).inc()
        REQUEST_LATENCY.labels(method=request.method, path_template=path_template).observe(duration)

        if response.status_code == 409 and path_template.endswith("/bookings"):
            BOOKING_CAPACITY_REJECTIONS.inc()
        if response.status_code == 409 and path_template.endswith("/seed-purchases"):
            SEED_STOCK_REJECTIONS.inc()

        return response


async def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
