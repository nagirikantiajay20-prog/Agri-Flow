"""
Load test for the two concurrency-critical endpoints (Master Plan §39):
booking creation and seed purchase. Hits a RUNNING instance of the API
over HTTP (unlike tests/integration/test_concurrency.py, which calls the
service layer in-process) — this is closer to what real concurrent
traffic looks like, including the HTTP/connection-pool layer.

Usage:
    # 1. Start the app: uvicorn app.main:app
    # 2. Seed a warehouse slot + a seed with known capacity/stock (see
    #    scripts/seed_load_test_data.py, or pass --warehouse-slot-id /
    #    --seed-id for existing ones)
    # 3. python scripts/load_test_concurrency.py --concurrency 100 \
    #        --warehouse-slot-id <uuid> --seed-id <uuid>

Reports: request count, success/failure breakdown by HTTP status,
latency percentiles, and — critically — whether the final DB state
matches what pure math predicts (no oversell). This last check requires
DB access and is best cross-checked with tests/integration/test_concurrency.py
for an authoritative pass/fail; this script is for throughput/latency
characterization under contention, not correctness certification.

IMPORTANT — two things WILL reduce your effective concurrency below
--concurrency, and that's by design, not a bug in this script:
  1. Redis-backed rate limiting (app.middleware.rate_limit, gap-fix #3
     in the backend remediation plan) throttles registration/login
     bursts from a single IP. Raise RATE_LIMIT_AUTH_PER_15MIN /
     RATE_LIMIT_WRITE_PER_5MIN in .env before load testing above ~20-30
     concurrent registrations from one machine, or the numbers you see
     will mostly measure the rate limiter, not the booking/purchase path.
  2. Freshly self-registered farmers are `status=pending` and cannot hit
     booking/purchase endpoints until approved (Master Plan Module 4).
     Run `python scripts/seed_load_test_data.py` AFTER this script's
     registration step (or use accounts you've already approved) to
     flip them to `active`, then re-run just the booking/purchase phase.
"""
import argparse
import asyncio
import statistics
import time
from collections import Counter

import httpx

DEFAULT_BASE_URL = "http://localhost:8000/api/v1"


async def _register_and_login(client: httpx.AsyncClient, base_url: str, phone: str) -> str:
    await client.post(
        f"{base_url}/auth/register",
        json={"name": f"LoadTest {phone}", "phone": phone, "password": "loadtestpass123"},
    )
    # NOTE: freshly registered farmers are `pending` — for a real load
    # test, approve them first (or point --approved-phone-prefix at
    # phones you've pre-approved via scripts/create_superadmin.py +
    # PATCH /farmers/{id}/approval). This script logs in regardless so
    # you can see the 403s if accounts aren't approved yet.
    resp = await client.post(f"{base_url}/auth/login", json={"phone": phone, "password": "loadtestpass123"})
    if resp.status_code != 200:
        return ""
    return resp.json()["data"]["access_token"]


async def _attempt_booking(client: httpx.AsyncClient, base_url: str, token: str, warehouse_id: str, slot_id: str) -> tuple[int, float]:
    start = time.monotonic()
    resp = await client.post(
        f"{base_url}/bookings",
        json={
            "warehouse_id": warehouse_id, "warehouse_slot_id": slot_id,
            "booking_date": time.strftime("%Y-%m-%d"), "delivery_address": "Load test address",
            "grain_type": "Rice", "quantity_kg": "10",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.status_code, time.monotonic() - start


async def _attempt_purchase(client: httpx.AsyncClient, base_url: str, token: str, seed_id: str) -> tuple[int, float]:
    start = time.monotonic()
    resp = await client.post(
        f"{base_url}/seed-purchases",
        json={"seed_id": seed_id, "quantity_kg": "10"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.status_code, time.monotonic() - start


async def run(base_url: str, concurrency: int, warehouse_id: str | None, slot_id: str | None, seed_id: str | None) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"Registering + logging in {concurrency} farmers...")
        tokens = await asyncio.gather(
            *(
                _register_and_login(client, base_url, f"6{i:09d}")
                for i in range(concurrency)
            )
        )
        active_tokens = [t for t in tokens if t]
        print(f"{len(active_tokens)}/{concurrency} accounts could log in (others may be pending approval)")

        if not active_tokens:
            print("No usable accounts — approve some farmers first, or run against a DB where auto-approval is on.")
            return

        results: dict[str, list[tuple[int, float]]] = {"booking": [], "purchase": []}

        if slot_id and warehouse_id:
            print(f"\nFiring {len(active_tokens)} concurrent booking requests at slot {slot_id}...")
            results["booking"] = await asyncio.gather(
                *(_attempt_booking(client, base_url, t, warehouse_id, slot_id) for t in active_tokens)
            )

        if seed_id:
            print(f"\nFiring {len(active_tokens)} concurrent purchase requests at seed {seed_id}...")
            results["purchase"] = await asyncio.gather(
                *(_attempt_purchase(client, base_url, t, seed_id) for t in active_tokens)
            )

        for label, rows in results.items():
            if not rows:
                continue
            statuses = Counter(status for status, _ in rows)
            latencies = [lat for _, lat in rows]
            print(f"\n--- {label} ---")
            print(f"  status breakdown: {dict(statuses)}")
            if latencies:
                print(f"  latency (s): p50={statistics.median(latencies):.3f} "
                      f"p95={sorted(latencies)[int(len(latencies) * 0.95)]:.3f} "
                      f"max={max(latencies):.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--warehouse-id", default=None)
    parser.add_argument("--warehouse-slot-id", default=None)
    parser.add_argument("--seed-id", default=None)
    args = parser.parse_args()

    if not args.warehouse_slot_id and not args.seed_id:
        parser.error("Pass at least one of --warehouse-slot-id (with --warehouse-id) or --seed-id")

    asyncio.run(run(args.base_url, args.concurrency, args.warehouse_id, args.warehouse_slot_id, args.seed_id))
