import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings

BASE_URL = "http://localhost:8000/api/v1" if hasattr(settings, "API_BASE_URL") else "https://agri-flow-backend.onrender.com/api/v1"

async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Login
        login_res = await client.post(f"{BASE_URL}/auth/login", json={"phone": "9876543210", "password": "FarmerTestPass123"})
        print(f"Login Status: {login_res.status_code}")
        if login_res.status_code != 200:
            # Try with production URL if local server isn't running
            BASE_URL_PROD = "https://agri-flow-backend.onrender.com/api/v1"
            print(f"Retrying login against production URL: {BASE_URL_PROD}")
            login_res = await client.post(f"{BASE_URL_PROD}/auth/login", json={"phone": "9876543210", "password": "FarmerTestPass123"})
            print(f"Prod Login Status: {login_res.status_code}")

        data = login_res.json()["data"]
        token = data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"Login User: {data['user']['name']} ({data['user']['role']})")

        # 2. Profile
        prof_res = await client.get(f"{BASE_URL}/farmer/profile", headers=headers)
        print(f"Profile Status: {prof_res.status_code} -> {prof_res.json()['data']['name']}")

        # 3. Crops
        crops_res = await client.get(f"{BASE_URL}/farmer/crops", headers=headers)
        print(f"Crops Status: {crops_res.status_code} -> {len(crops_res.json()['data'])} crops")

        # 4. Warehouses
        wh_res = await client.get(f"{BASE_URL}/farmer/warehouses", headers=headers)
        print(f"Warehouses Status: {wh_res.status_code} -> {len(wh_res.json()['data'])} warehouses")

        # 5. Dashboard
        dash_res = await client.get(f"{BASE_URL}/farmer/dashboard", headers=headers)
        print(f"Dashboard Status: {dash_res.status_code}")

        # 6. Verify Admin/Manager endpoint blocked (403 Forbidden)
        admin_res = await client.get(f"{BASE_URL}/admin/farmers", headers=headers)
        print(f"Admin Endpoint Blocked Check: {admin_res.status_code} (Expected 403)")

if __name__ == "__main__":
    asyncio.run(main())
