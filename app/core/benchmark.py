"""
Shared definition of what a "page load" costs.

Both the CI budget test (tests/performance/test_page_load_budget.py) and
the reporting script (scripts/benchmark_pageloads.py) import PAGES from
here, so the budget enforced in CI and the numbers quoted in a report can
never drift apart.

A page is listed as the set of API calls the web app and the mobile app
issue to render it. The budget applies to the whole set, because that is
what the user actually waits for — measuring one endpoint at a time
hides the case where a screen is fast per call but issues eight of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PAGE_LOAD_BUDGET_MS = 2000.0

# Server-side budget. The 2s requirement is end-to-end and has to cover
# TLS, network RTT on a mobile connection, and client render, so the API
# is held to a much tighter share of it.
SERVER_BUDGET_MS = 400.0


@dataclass(frozen=True)
class Page:
    name: str
    role: str
    requests: tuple[str, ...]
    concurrent: bool = True
    notes: str = field(default="")


PAGES: tuple[Page, ...] = (
    Page(
        name="Public landing",
        role="anonymous",
        requests=(
            "GET /api/v1/public/stats",
            "GET /api/v1/public/market-rates",
            "GET /api/v1/public/seeds",
        ),
    ),
    Page(
        name="Farmer dashboard",
        role="farmer",
        requests=(
            "GET /api/v1/farmers/me/dashboard",
            "GET /api/v1/notifications?page=1&page_size=20",
        ),
    ),
    Page(
        name="Farmer crops",
        role="farmer",
        requests=("GET /api/v1/crops", "GET /api/v1/farm-visits"),
    ),
    Page(
        name="Farmer marketplace",
        role="farmer",
        requests=("GET /api/v1/seeds", "GET /api/v1/seed-purchases?page=1&page_size=20"),
    ),
    Page(
        name="Farmer bookings",
        role="farmer",
        requests=(
            "GET /api/v1/bookings?page=1&page_size=20",
            "GET /api/v1/warehouses",
            "GET /api/v1/warehouse-slots",
        ),
    ),
    Page(
        name="Farmer wallet",
        role="farmer",
        requests=("GET /api/v1/transactions?page=1&page_size=20", "GET /api/v1/grain-sales?page=1&page_size=20"),
    ),
    Page(
        name="Farmer profile",
        role="farmer",
        requests=("GET /api/v1/farmers/me", "GET /api/v1/users/me"),
    ),
    Page(
        name="Admin operational dashboard",
        role="super_admin",
        requests=(
            "GET /api/v1/admin/dashboard",
            "GET /api/v1/market-rates",
            "GET /api/v1/warehouses",
        ),
        notes="The screen the 'real-time operational dashboard' requirement refers to",
    ),
    Page(
        name="Admin farmers list",
        role="super_admin",
        requests=("GET /api/v1/farmers?page=1&page_size=20",),
    ),
    Page(
        name="Admin procurement queue",
        role="super_admin",
        requests=(
            "GET /api/v1/grain-sales?page=1&page_size=20",
            "GET /api/v1/bookings?page=1&page_size=20",
        ),
    ),
    Page(
        name="Admin visits planner",
        role="super_admin",
        requests=("GET /api/v1/farm-visits", "GET /api/v1/crops/active"),
    ),
    Page(
        name="Admin finance",
        role="super_admin",
        requests=(
            "GET /api/v1/transactions?page=1&page_size=20",
            "GET /api/v1/seed-purchases?page=1&page_size=20",
        ),
    ),
    Page(
        name="Super admin governance",
        role="super_admin",
        requests=(
            "GET /api/v1/managers?page=1&page_size=20",
            "GET /api/v1/admin/audit-logs?page=1&page_size=20",
            "GET /api/v1/farmers/bank-change-requests?page=1&page_size=20",
        ),
    ),
)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * len(ordered) + 0.5) - 1))
    return ordered[index]
