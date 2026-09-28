import asyncio
import os
import sys
import time
import numpy as np

# Ensure local test database
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:password@localhost:5432/agriflow_test"
os.environ["DATABASE_URL_SYNC"] = "postgresql+psycopg2://postgres:password@localhost:5432/agriflow_test"
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET_KEY"] = "benchmark-secret-key-12345678901234567890"

sys.path.insert(0, r"c:\Users\HP\.gemini\antigravity-ide\scratch\agriflow-backend")

from sqlalchemy import select
from httpx import ASGITransport, AsyncClient

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import hash_password, verify_password
from app.models.enums import UserRole, UserStatus
from app.models.user import FarmerProfile, User
from app.services import auth_service
import app.main as m

TEST_PHONE = "9990001234"
TEST_PASS = "BenchmarkPass123!"

async def setup_test_user():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.phone == TEST_PHONE))
        user = res.scalar_one_or_none()
        if not user:
            user = User(
                name="Benchmark Farmer",
                phone=TEST_PHONE,
                password_hash=hash_password(TEST_PASS),
                role=UserRole.FARMER,
                status=UserStatus.ACTIVE,
            )
            db.add(user)
            await db.flush()
            profile = FarmerProfile(user_id=user.id, acres_of_land=10.0)
            db.add(profile)
            await db.commit()
            print("Created local test user for benchmarking.")
        else:
            print("Local test user exists.")

def calc_stats(times_ms):
    arr = np.array(times_ms)
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }

async def run_benchmark(iterations=100):
    await setup_test_user()

    db_times = []
    verify_times = []
    jwt_times = []
    full_times = []

    # Warmup
    print(f"Running 5 warmup iterations...")
    transport = ASGITransport(app=m.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            await client.post("/api/v1/farmer/auth/login", json={"phone": TEST_PHONE, "password": TEST_PASS})

    print(f"Running {iterations} benchmark iterations...")

    # Individual component measurements
    async with AsyncSessionLocal() as db:
        for _ in range(iterations):
            # A. DB User Lookup
            t0 = time.perf_counter()
            res = await db.execute(select(User).where(User.phone == TEST_PHONE))
            user = res.scalar_one()
            t1 = time.perf_counter()
            db_times.append((t1 - t0) * 1000)

            # B. Password Verification
            t0 = time.perf_counter()
            is_valid = verify_password(TEST_PASS, user.password_hash)
            t1 = time.perf_counter()
            assert is_valid
            verify_times.append((t1 - t0) * 1000)

            # C. JWT Creation & Refresh Token
            t0 = time.perf_counter()
            await auth_service.issue_token_pair(db, user=user)
            await db.flush()
            t1 = time.perf_counter()
            jwt_times.append((t1 - t0) * 1000)

    # D. Complete login request via HTTP client
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(iterations):
            t0 = time.perf_counter()
            resp = await client.post("/api/v1/farmer/auth/login", json={"phone": TEST_PHONE, "password": TEST_PASS})
            t1 = time.perf_counter()
            assert resp.status_code == 200
            full_times.append((t1 - t0) * 1000)

    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS (BASELINE - BEFORE OPTIMIZATION)")
    print("=" * 60)
    for name, data in [
        ("A. DB User Lookup", db_times),
        ("B. Argon2id Verification", verify_times),
        ("C. JWT / Token Pair Issue", jwt_times),
        ("D. Complete Login Request", full_times),
    ]:
        s = calc_stats(data)
        print(f"\n{name} ({len(data)} iterations):")
        print(f"  Mean:   {s['mean']:.2f} ms")
        print(f"  Median: {s['median']:.2f} ms")
        print(f"  P95:    {s['p95']:.2f} ms")
        print(f"  P99:    {s['p99']:.2f} ms")
        print(f"  Min:    {s['min']:.2f} ms")
        print(f"  Max:    {s['max']:.2f} ms")

if __name__ == "__main__":
    asyncio.run(run_benchmark(50))
