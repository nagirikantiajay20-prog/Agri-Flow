import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:password@localhost:5432/agriflow_test"
os.environ["DATABASE_URL_SYNC"] = "postgresql+psycopg2://postgres:password@localhost:5432/agriflow_test"
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET_KEY"] = "farmer-flow-secret-key-1234567890123"

sys.path.insert(0, r"c:\Users\HP\.gemini\antigravity-ide\scratch\agriflow-backend")

import fakeredis.aioredis
from app.core import redis as redis_module

fake_redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
redis_module._client = fake_redis_client
redis_module.get_redis = lambda: fake_redis_client
redis_module.mark_available()

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import hash_password
from app.models.enums import UserRole, UserStatus
from app.models.user import FarmerProfile, User
import app.main as m

TEST_PHONE = "9876543299"
TEST_PASS = "FarmerFlowPass123!"

async def run_flow():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 0. Setup test farmer
    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.phone == TEST_PHONE))
        await db.commit()

        user = User(
            name="Flow Farmer",
            phone=TEST_PHONE,
            password_hash=hash_password(TEST_PASS),
            role=UserRole.FARMER,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()
        db.add(FarmerProfile(user_id=user.id, acres_of_land=15.0, farm_name="Flow Farm"))
        await db.commit()

    transport = ASGITransport(app=m.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        print("\n--- Starting Farmer Flow Verification ---")
        
        # 1. Farmer Login
        login_res = await client.post("/api/v1/farmer/auth/login", json={"phone": TEST_PHONE, "password": TEST_PASS})
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        data = login_res.json()["data"]
        token = data["access_token"]
        refresh_token = data["refresh_token"]
        print("1. [PASS] Farmer Login -> Token generated")

        headers = {"Authorization": f"Bearer {token}"}

        # 2. Dashboard
        dash_res = await client.get("/api/v1/farmer/dashboard", headers=headers)
        assert dash_res.status_code == 200, f"Dashboard failed: {dash_res.text}"
        print("2. [PASS] Dashboard loaded successfully")

        # 3. Seeds
        seeds_res = await client.get("/api/v1/farmer/seeds", headers=headers)
        assert seeds_res.status_code == 200, f"Seeds failed: {seeds_res.text}"
        print("3. [PASS] Seed Catalog loaded successfully")

        # 4. Crops
        crops_res = await client.get("/api/v1/farmer/crops", headers=headers)
        assert crops_res.status_code == 200, f"Crops failed: {crops_res.text}"
        print("4. [PASS] Crops list loaded successfully")

        # 5. Grain Sales Offers
        sales_res = await client.get("/api/v1/farmer/grain-sales/offers", headers=headers)
        assert sales_res.status_code == 200, f"Grain sales failed: {sales_res.text}"
        print("5. [PASS] Grain Sales Offers loaded successfully")

        # 6. Transactions
        tx_res = await client.get("/api/v1/farmer/transactions", headers=headers)
        assert tx_res.status_code == 200, f"Transactions failed: {tx_res.text}"
        print("6. [PASS] Transactions loaded successfully")

        # 7. Profile
        prof_res = await client.get("/api/v1/farmer/profile", headers=headers)
        assert prof_res.status_code == 200, f"Profile failed: {prof_res.text}"
        print("7. [PASS] Profile loaded successfully")

        # 8. Logout
        logout_res = await client.post("/api/v1/farmer/auth/logout", headers=headers)
        assert logout_res.status_code == 200, f"Logout failed: {logout_res.text}"
        print("8. [PASS] Logout completed successfully")

        # 9. Login again
        relogin_res = await client.post("/api/v1/farmer/auth/login", json={"phone": TEST_PHONE, "password": TEST_PASS})
        assert relogin_res.status_code == 200, f"Relogin failed: {relogin_res.text}"
        print("9. [PASS] Login again verified successfully")

        print("\nAll 9 steps in Farmer Workflow verified perfectly!")

if __name__ == "__main__":
    asyncio.run(run_flow())
