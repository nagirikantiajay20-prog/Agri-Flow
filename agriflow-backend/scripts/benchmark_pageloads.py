"""
Measure page-load latency against a running AgriFlow API.

The CI budget test (tests/performance/test_page_load_budget.py) measures
the server's share in-process, which excludes TLS, network RTT and the
connection pooler. This script drives the same PAGES definition over real
HTTP so the end-to-end 2-second requirement can be checked against
staging or production from a representative client.

    python scripts/benchmark_pageloads.py \\
        --base-url https://api.example.com \\
        --farmer-phone 9000000001 --farmer-password ... \\
        --admin-phone 9999999999 --admin-password ... \\
        --rounds 20

Add --slow-network to simulate a mobile connection by adding a fixed
delay to every request, which is the condition the 2s target is really
about.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.core.benchmark import PAGE_LOAD_BUDGET_MS, PAGES, percentile  # noqa: E402


async def login(client: httpx.AsyncClient, base_url: str, phone: str, password: str) -> str | None:
    if not phone or not password:
        return None
    resp = await client.post(
        f"{base_url}/api/v1/auth/login", json={"phone": phone, "password": password}
    )
    if resp.status_code != 200:
        print(f"  ! login failed for {phone}: {resp.status_code} {resp.text[:160]}")
        return None
    return resp.json()["data"]["access_token"]


async def load_page(client, base_url, page, headers, extra_delay_ms: float) -> tuple[float, list[int]]:
    async def one(request: str):
        method, path = request.split(" ", 1)
        if extra_delay_ms:
            await asyncio.sleep(extra_delay_ms / 1000.0)
        return await client.request(method, f"{base_url}{path}", headers=headers)

    started = time.perf_counter()
    responses = await asyncio.gather(*(one(r) for r in page.requests), return_exceptions=True)
    elapsed = (time.perf_counter() - started) * 1000

    codes = []
    for r in responses:
        codes.append(0 if isinstance(r, Exception) else r.status_code)
    return elapsed, codes


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--farmer-phone", default="")
    parser.add_argument("--farmer-password", default="")
    parser.add_argument("--manager-phone", default="")
    parser.add_argument("--manager-password", default="")
    parser.add_argument("--admin-phone", default="")
    parser.add_argument("--admin-password", default="")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument(
        "--slow-network",
        type=float,
        default=0.0,
        metavar="MS",
        help="Add this many ms to every request to emulate a mobile connection",
    )
    parser.add_argument("--budget-ms", type=float, default=PAGE_LOAD_BUDGET_MS)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30.0) as client:
        tokens = {
            "farmer": await login(client, base_url, args.farmer_phone, args.farmer_password),
            "manager": await login(client, base_url, args.manager_phone, args.manager_password),
            "super_admin": await login(client, base_url, args.admin_phone, args.admin_password),
        }

        print(f"\nAgriFlow page-load benchmark — {base_url}")
        print(f"budget {args.budget_ms:.0f}ms per page, {args.rounds} rounds")
        if args.slow_network:
            print(f"emulating +{args.slow_network:.0f}ms per request")
        print("-" * 78)
        print(f"{'page':34s} {'p50':>9s} {'p95':>9s} {'p99':>9s}  {'verdict':>8s}")
        print("-" * 78)

        failures = 0
        for page in PAGES:
            token = tokens.get(page.role)
            if page.role != "anonymous" and token is None:
                print(f"{page.name:34s} {'-':>9s} {'-':>9s} {'-':>9s}  {'SKIP':>8s}")
                continue
            headers = {"Authorization": f"Bearer {token}"} if token else {}

            for _ in range(args.warmup):
                await load_page(client, base_url, page, headers, args.slow_network)

            samples: list[float] = []
            bad: list[int] = []
            for _ in range(args.rounds):
                elapsed, codes = await load_page(client, base_url, page, headers, args.slow_network)
                samples.append(elapsed)
                bad.extend(c for c in codes if c == 0 or c >= 400)

            p50 = statistics.median(samples)
            p95 = percentile(samples, 95)
            p99 = percentile(samples, 99)
            ok = p95 < args.budget_ms and not bad
            failures += 0 if ok else 1
            verdict = "OK" if ok else ("ERRORS" if bad else "OVER")
            print(f"{page.name:34s} {p50:8.1f}ms {p95:8.1f}ms {p99:8.1f}ms  {verdict:>8s}")
            if bad:
                print(f"{'':34s}   -> non-2xx/failed responses: {sorted(set(bad))}")

        print("-" * 78)
        print("all pages within budget" if not failures else f"{failures} page(s) outside budget")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
