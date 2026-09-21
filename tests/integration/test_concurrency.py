"""
Concurrency tests — Master Plan §12/§39: the highest-priority test
category in the whole plan, because this is the exact race the legacy
`SELECT ... FOR UPDATE` locking (confirmed present in the shipped
migrations) was built to prevent, and it has already caused a real
production bug once (unit-mismatch fix in
20260714060000_fix_booking_notification_quintals.sql).

Expected result (Master Plan §39): capacity must never go negative or
be oversold, regardless of how many requests race for it.
"""
import asyncio
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.enums import BookingStatus, UserRole, UserStatus
from app.models.seed import Seed
from app.models.user import User
from app.models.warehouse import BookingSlot, Warehouse, WarehouseSlot
from app.services import booking_service, purchase_service

pytestmark = pytest.mark.asyncio


async def _make_farmer(n: int) -> User:
    async with AsyncSessionLocal() as db:
        user = User(
            name=f"Concurrency Farmer {n}",
            phone=f"7{n:09d}",
            password_hash=hash_password("testpass123"),
            role=UserRole.FARMER,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def test_booking_slot_never_oversold():
    # Slot has capacity for exactly 100 kg.
    async with AsyncSessionLocal() as db:
        warehouse = Warehouse(name="Race Warehouse", address="Test", total_capacity_kg=Decimal("1000"))
        db.add(warehouse)
        await db.flush()
        slot = WarehouseSlot(
            warehouse_id=warehouse.id,
            slot_date=date.today(),
            start_time=time(9, 0),
            end_time=time(11, 0),
            capacity_kg=Decimal("100"),
            booked_kg=Decimal("0"),
            status="active",
        )
        db.add(slot)
        await db.commit()
        warehouse_id, slot_id = warehouse.id, slot.id

    # 20 farmers each try to book 10 kg concurrently — exactly 10 should
    # succeed (100kg / 10kg), the rest must get a clean CapacityExceededError,
    # never an oversold slot.
    farmers = [await _make_farmer(1000 + i) for i in range(20)]

    async def attempt(farmer: User):
        async with AsyncSessionLocal() as db:
            try:
                await booking_service.create_booking(
                    db,
                    farmer=farmer,
                    warehouse_id=warehouse_id,
                    warehouse_slot_id=slot_id,
                    booking_date=date.today(),
                    delivery_address="Test address",
                    grain_type="Rice",
                    quantity_kg=Decimal("10"),
                    grain_sale_id=None,
                    notes=None,
                )
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                return False

    results = await asyncio.gather(*(attempt(f) for f in farmers))
    successes = sum(1 for r in results if r)

    assert successes == 10, f"Expected exactly 10 successful bookings, got {successes}"

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WarehouseSlot).where(WarehouseSlot.id == slot_id))
        final_slot = result.scalar_one()
        assert final_slot.booked_kg == Decimal("100"), "Slot capacity must be exactly filled, never exceeded"

        booking_count = await db.execute(
            select(BookingSlot).where(BookingSlot.warehouse_slot_id == slot_id, BookingSlot.status == BookingStatus.PENDING)
        )
        assert len(booking_count.scalars().all()) == 10


async def test_seed_stock_never_oversold():
    async with AsyncSessionLocal() as db:
        seed = Seed(name="Race Seed", price_per_kg=Decimal("50.00"), stock_kg=Decimal("50"), is_active=True)
        db.add(seed)
        await db.commit()
        seed_id = seed.id

    farmers = [await _make_farmer(2000 + i) for i in range(10)]

    async def attempt(farmer: User):
        async with AsyncSessionLocal() as db:
            try:
                await purchase_service.purchase_seeds(
                    db, farmer=farmer, seed_id=seed_id, quantity_kg=Decimal("10"),
                    warehouse_id=None, payment_method=None, upi_id=None,
                )
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                return False

    results = await asyncio.gather(*(attempt(f) for f in farmers))
    successes = sum(1 for r in results if r)

    # 50kg stock / 10kg per purchase = exactly 5 should succeed.
    assert successes == 5, f"Expected exactly 5 successful purchases, got {successes}"

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Seed).where(Seed.id == seed_id))
        final_seed = result.scalar_one()
        assert final_seed.stock_kg == Decimal("0"), "Stock must never go negative"
