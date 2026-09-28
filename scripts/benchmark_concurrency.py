import asyncio
import os
import sys
import time
import numpy as np

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:password@localhost:5432/agriflow_test"
os.environ["DATABASE_URL_SYNC"] = "postgresql+psycopg2://postgres:password@localhost:5432/agriflow_test"
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET_KEY"] = "benchmark-secret-key-12345678901234567890"

sys.path.insert(0, r"c:\Users\HP\.gemini\antigravity-ide\scratch\agriflow-backend")

from httpx import ASGITransport, AsyncClient
import app.main as m

TEST_PHONE = "9990001234"
TEST_PASS = "BenchmarkPass123!"

async def monitor_event_loop_lag(stop_event, lags):
    """Measures event loop delay by scheduling a 10ms sleep and checking actual elapsed time."""
    while not stop_event.is_set():
        t0 = time.perf_counter()
        await asyncio.sleep(0.01)  # 10ms target
        elapsed = (time.perf_counter() - t0) * 1000  # ms
        lag = max(0.0, elapsed - 10.0)  # lag above expected 10ms
        lags.append(lag)

async def run_concurrent_logins(concurrency_level):
    transport = ASGITransport(app=m.app)
    stop_event = asyncio.Event()
    loop_lags = []
    
    monitor_task = asyncio.create_task(monitor_event_loop_lag(stop_event, loop_lags))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Also run background lightweight requests simulating other farmer requests (e.g. health check)
        bg_latencies = []
        async def background_pinger():
            while not stop_event.is_set():
                t0 = time.perf_counter()
                try:
                    res = await client.get("/health/ready")
                    elapsed = (time.perf_counter() - t0) * 1000
                    bg_latencies.append(elapsed)
                except Exception:
                    pass
                await asyncio.sleep(0.02)

        bg_task = asyncio.create_task(background_pinger())

        latencies = []
        errors = 0

        async def single_login():
            nonlocal errors
            t0 = time.perf_counter()
            try:
                res = await client.post("/api/v1/farmer/auth/login", json={"phone": TEST_PHONE, "password": TEST_PASS})
                t1 = time.perf_counter()
                if res.status_code == 200:
                    latencies.append((t1 - t0) * 1000)
                else:
                    errors += 1
            except Exception as e:
                errors += 1

        t_start = time.perf_counter()
        # Fire `concurrency_level` login requests simultaneously
        await asyncio.gather(*(single_login() for _ in range(concurrency_level)))
        total_wall_time = (time.perf_counter() - t_start) * 1000

        stop_event.set()
        await monitor_task
        await bg_task

    lat_arr = np.array(latencies) if latencies else np.array([0])
    lag_arr = np.array(loop_lags) if loop_lags else np.array([0])
    bg_arr = np.array(bg_latencies) if bg_latencies else np.array([0])

    return {
        "concurrency": concurrency_level,
        "total_wall_time_ms": total_wall_time,
        "success": len(latencies),
        "errors": errors,
        "login_mean_ms": float(np.mean(lat_arr)),
        "login_p50_ms": float(np.median(lat_arr)),
        "login_p95_ms": float(np.percentile(lat_arr, 95)),
        "login_p99_ms": float(np.percentile(lat_arr, 99)),
        "max_loop_lag_ms": float(np.max(lag_arr)),
        "mean_loop_lag_ms": float(np.mean(lag_arr)),
        "bg_requests_mean_ms": float(np.mean(bg_arr)),
        "bg_requests_p95_ms": float(np.percentile(bg_arr, 95)),
    }

async def main():
    levels = [1, 5, 10, 25]
    print(f"{'Conc':<6} | {'Total(ms)':<10} | {'Mean(ms)':<10} | {'P50(ms)':<10} | {'P95(ms)':<10} | {'Loop Lag Max(ms)':<18} | {'BG Req P95(ms)':<15} | Errors")
    print("-" * 95)
    for c in levels:
        r = await run_concurrent_logins(c)
        print(f"{r['concurrency']:<6} | {r['total_wall_time_ms']:<10.1f} | {r['login_mean_ms']:<10.1f} | {r['login_p50_ms']:<10.1f} | {r['login_p95_ms']:<10.1f} | {r['max_loop_lag_ms']:<18.1f} | {r['bg_requests_p95_ms']:<15.1f} | {r['errors']}")

if __name__ == "__main__":
    asyncio.run(main())
