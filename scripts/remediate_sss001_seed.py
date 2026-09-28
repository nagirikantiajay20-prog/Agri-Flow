"""
Safe database remediation script for SSS-001.

Targets EXACT row by primary key: b63cf216-c449-4688-aece-cc4ec9c89176
Verifies current name is 'light' before applying correction.
"""
import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.seed import Seed

TARGET_SEED_ID = "b63cf216-c449-4688-aece-cc4ec9c89176"

async def remediate():
    async with AsyncSessionLocal() as session:
        seed = (await session.execute(
            select(Seed).where(Seed.id == TARGET_SEED_ID)
        )).scalar_one_or_none()

        if not seed:
            print("ERROR: Target seed record not found.")
            return False

        print(f"BEFORE: ID={seed.id}, name={repr(seed.name)}, crop_type={repr(seed.crop_type)}, variety={repr(seed.variety)}, image_url={repr(seed.image_url)}")

        if seed.name.strip().lower() != "light":
            print(f"SKIPPING: Seed name is not 'light' (currently: {repr(seed.name)})")
            return False

        # Correct the placeholder name and invalid webpage link
        seed.name = "Sweet Corn (Hybrid Maize)"
        seed.crop_type = "Maize"
        seed.variety = "Sugar-75 Sweet Corn"
        seed.description = "High-yielding hybrid sweet corn seeds with uniform golden cobs and superior sweetness."
        # Clear webpage link so mobile app falls back to dedicated photorealistic asset
        seed.image_url = None

        await session.commit()
        await session.refresh(seed)

        print(f"AFTER: ID={seed.id}, name={repr(seed.name)}, crop_type={repr(seed.crop_type)}, variety={repr(seed.variety)}, image_url={repr(seed.image_url)}")
        return True

if __name__ == "__main__":
    asyncio.run(remediate())
