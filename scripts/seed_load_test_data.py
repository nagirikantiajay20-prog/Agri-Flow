"""
Seeds one warehouse + slot and one seed record for use with
scripts/load_test_concurrency.py, and prints the IDs to pass to it.
Also approves any `pending` farmers so the load test's freshly
registered accounts can actually hit the protected endpoints.

Usage: python scripts/seed_load_test_data.py [--capacity-kg 500] [--stock-kg 500]
"""
import argparse
import asyncio
import sys
from datetime import date, time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import update  # noqa: E402

from app.core.database import session_scope  # noqa: E402
from app.models.enums import UserRole, UserStatus  # noqa: E402
from app.models.seed import Seed  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.warehouse import Warehouse, WarehouseSlot  # noqa: E402


async def main(capacity_kg: Decimal, stock_kg: Decimal) -> None:
    async with session_scope() as db:
        warehouse = Warehouse(name="Load Test Warehouse", address="N/A", total_capacity_kg=capacity_kg * 10)
        db.add(warehouse)
        await db.flush()
        slot = WarehouseSlot(
            warehouse_id=warehouse.id, slot_date=date.today(), start_time=time(9, 0), end_time=time(17, 0),
            capacity_kg=capacity_kg, booked_kg=Decimal("0"), status="active",
        )
        db.add(slot)

        seed = Seed(name="Load Test Seed", price_per_kg=Decimal("10.00"), stock_kg=stock_kg, is_active=True)
        db.add(seed)
        await db.flush()

        # Approve any pending farmers registered by the load test script
        # in a previous run, so this run's freshly-registered ones (or
        # leftover pending ones) can actually authenticate against
        # protected endpoints.
        result = await db.execute(
            update(User).where(User.role == UserRole.FARMER, User.status == UserStatus.PENDING).values(status=UserStatus.ACTIVE)
        )

        print(f"warehouse_id={warehouse.id}")
        print(f"warehouse_slot_id={slot.id} (capacity_kg={capacity_kg})")
        print(f"seed_id={seed.id} (stock_kg={stock_kg})")
        print(f"approved {result.rowcount or 0} previously-pending farmer(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity-kg", type=Decimal, default=Decimal("500"))
    parser.add_argument("--stock-kg", type=Decimal, default=Decimal("500"))
    args = parser.parse_args()
    asyncio.run(main(args.capacity_kg, args.stock_kg))
