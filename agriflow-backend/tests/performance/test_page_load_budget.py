"""
Page-load budget enforcement.

Fails CI when any screen of the web or mobile app would take longer than
its share of the 2-second requirement to assemble server-side. The
dataset is seeded once per session (tests/performance/seed_dataset.py) to
a size where an unindexed query is visibly slower than an indexed one.

Marked `performance` so it can be deselected on a laptop
(`pytest -m "not performance"`) while still gating merges in CI.
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from app.core.benchmark import PAGES, SERVER_BUDGET_MS, percentile
from app.core.database import AsyncSessionLocal
from tests.performance.seed_dataset import seed
from tests.utils import auth_headers

pytestmark = [pytest.mark.asyncio, pytest.mark.performance]

WARMUP_ROUNDS = 2
MEASURED_ROUNDS = 12


@pytest_asyncio.fixture(scope="session")
async def benchmark_principals():
    async with AsyncSessionLocal() as db:
        return await seed(db)


def _headers(page_role: str, principals) -> dict:
    if page_role == "anonymous":
        return {}
    return auth_headers(principals[{"farmer": "farmer", "manager": "manager", "super_admin": "admin"}[page_role]])


async def _load_page(client, page, headers) -> tuple[float, list[int]]:
    started = time.perf_counter()
    calls = []
    for request in page.requests:
        method, path = request.split(" ", 1)
        calls.append(client.request(method, path, headers=headers))
    responses = await asyncio.gather(*calls) if page.concurrent else [await c for c in calls]
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, [r.status_code for r in responses]


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
async def test_page_assembles_within_budget(client, fake_redis, benchmark_principals, page):
    headers = _headers(page.role, benchmark_principals)

    for _ in range(WARMUP_ROUNDS):
        _, statuses = await _load_page(client, page, headers)
        assert all(s < 400 for s in statuses), (
            f"{page.name}: a request failed before it could be timed -> {list(zip(page.requests, statuses))}"
        )

    samples = []
    for _ in range(MEASURED_ROUNDS):
        elapsed, statuses = await _load_page(client, page, headers)
        assert all(s < 400 for s in statuses), f"{page.name} returned {statuses}"
        samples.append(elapsed)

    p50 = percentile(samples, 50)
    p95 = percentile(samples, 95)
    print(f"\n{page.name:34s} p50={p50:7.1f}ms  p95={p95:7.1f}ms  budget={SERVER_BUDGET_MS:.0f}ms")

    assert p95 < SERVER_BUDGET_MS, (
        f"{page.name} p95 {p95:.1f}ms exceeds the {SERVER_BUDGET_MS:.0f}ms server budget "
        f"(the 2s end-to-end requirement has to also cover network and render)"
    )


async def test_admin_dashboard_is_a_single_round_trip(benchmark_principals):
    """The operational dashboard must not regress back into a query per
    tile — that is what put the legacy dashboard over budget."""
    from sqlalchemy import event

    from app.core.database import engine
    from app.services import admin_service

    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        async with AsyncSessionLocal() as db:
            counter["n"] = 0
            await admin_service.get_dashboard(db)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert counter["n"] == 1, f"admin dashboard issued {counter['n']} queries, expected 1"


async def test_authenticated_request_resolves_identity_in_one_query(benchmark_principals):
    """Identity resolution runs on every authenticated request, so any
    extra query here is multiplied across the entire API."""
    from sqlalchemy import event

    from app.core.database import engine
    from app.models.user import User

    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        async with AsyncSessionLocal() as db:
            counter["n"] = 0
            await db.get(User, benchmark_principals["farmer"].id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert counter["n"] == 1, (
        f"resolving the caller took {counter['n']} queries, expected 1 — check relationship "
        f"loader strategies in app/models/user.py"
    )
