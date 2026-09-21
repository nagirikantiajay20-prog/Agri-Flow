"""
Realistic dataset for the page-load budget.

Sized so the pagination, indexes and aggregate queries are exercised
against a table big enough for a sequential scan to look obviously
different from an index scan — benchmarking against five rows measures
nothing.
"""
from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.crop import Crop, FarmVisit
from app.models.enums import (
    BookingStatus,
    CropStatus,
    GrainGrade,
    GrainSaleStatus,
    NotificationType,
    PaymentStatus,
    TransactionDirection,
    TransactionReferenceType,
    TransactionStatus,
    UserRole,
    UserStatus,
    VisitStatus,
)
from app.models.grain import GrainSale
from app.models.ledger import AuditLog, BankChangeRequest, MarketRate, Notification, Transaction
from app.models.seed import Seed, SeedPurchase
from app.models.user import FarmerProfile, StaffProfile, User
from app.models.warehouse import BookingSlot, Warehouse, WarehouseSlot

FARMERS = 400
MANAGERS = 8
SEEDS = 40
WAREHOUSES = 12
SLOTS_PER_WAREHOUSE = 30
CROPS_PER_FARMER = 3
PURCHASES_PER_FARMER = 4
SALES_PER_FARMER = 3
BOOKINGS_PER_FARMER = 3
TRANSACTIONS_PER_FARMER = 6
NOTIFICATIONS_PER_FARMER = 8
AUDIT_LOGS = 5000

CROP_TYPES = ["Rice", "Wheat", "Maize", "Cotton", "Groundnut", "Sugarcane", "Turmeric", "Chili"]


def _phone(n: int) -> str:
    return f"9{n:09d}"


async def seed(db: AsyncSession, *, seed_value: int = 1337) -> dict:
    """Populates the benchmark dataset and returns the principals to
    authenticate as."""
    rng = random.Random(seed_value)
    today = date.today()

    admin = User(
        name="Bench Admin", phone=_phone(100_000_001), password_hash=hash_password("benchpass123"),
        role=UserRole.SUPER_ADMIN, status=UserStatus.ACTIVE,
    )
    db.add(admin)
    await db.flush()
    db.add(StaffProfile(user_id=admin.id, department="ops"))

    managers = []
    for i in range(MANAGERS):
        m = User(
            name=f"Bench Manager {i}", phone=_phone(200_000_000 + i),
            password_hash=hash_password("benchpass123"),
            role=UserRole.MANAGER, status=UserStatus.ACTIVE,
        )
        db.add(m)
        managers.append(m)
    await db.flush()
    for m in managers:
        db.add(StaffProfile(user_id=m.id, assigned_region="south"))

    seeds = [
        Seed(
            name=f"Seed {i}", variety=f"V{i}", price_per_kg=Decimal(rng.randrange(20, 400)),
            stock_kg=Decimal(rng.randrange(500, 20000)), is_active=True,
        )
        for i in range(SEEDS)
    ]
    db.add_all(seeds)

    warehouses = [
        Warehouse(
            name=f"Warehouse {i}", address=f"Plot {i}, Industrial Area",
            total_capacity_kg=Decimal("500000"), current_load_kg=Decimal(rng.randrange(0, 200000)),
        )
        for i in range(WAREHOUSES)
    ]
    db.add_all(warehouses)
    await db.flush()

    slots = []
    for w in warehouses:
        for d in range(SLOTS_PER_WAREHOUSE):
            slots.append(
                WarehouseSlot(
                    warehouse_id=w.id, slot_date=today + timedelta(days=d - 5),
                    start_time=__import__("datetime").time(9, 0),
                    end_time=__import__("datetime").time(12, 0),
                    capacity_kg=Decimal("20000"), booked_kg=Decimal(rng.randrange(0, 15000)),
                    status="active",
                )
            )
    db.add_all(slots)

    for crop_type in CROP_TYPES:
        for grade in GrainGrade:
            db.add(
                MarketRate(
                    crop_type=crop_type, grade=grade,
                    price_per_kg=Decimal(rng.randrange(15, 80)),
                    effective_date=today - timedelta(days=rng.randrange(0, 30)),
                    set_by=admin.id,
                )
            )
    await db.flush()

    farmers = []
    for i in range(FARMERS):
        status = UserStatus.ACTIVE if i % 10 else UserStatus.PENDING
        f = User(
            name=f"Bench Farmer {i}", phone=_phone(300_000_000 + i),
            password_hash=hash_password("benchpass123"),
            role=UserRole.FARMER, status=status,
        )
        db.add(f)
        farmers.append(f)
    await db.flush()

    rows: list = []
    for i, f in enumerate(farmers):
        rows.append(
            FarmerProfile(
                user_id=f.id, address=f"Village {i}", acres_of_land=Decimal(rng.randrange(1, 50)),
                bank_name="Bench Bank", account_number=f"ACC{i:08d}", ifsc_code="BENC0001234",
            )
        )
        if i % 20 == 0:
            rows.append(
                BankChangeRequest(
                    farmer_id=f.id, bank_name="New Bank", account_number=f"NEW{i:08d}",
                    ifsc_code="NEWB0001234",
                )
            )
        for c in range(CROPS_PER_FARMER):
            crop = Crop(
                farmer_id=f.id, crop_type=rng.choice(CROP_TYPES),
                acres=Decimal(rng.randrange(1, 20)),
                sowing_date=today - timedelta(days=rng.randrange(10, 200)),
                status=CropStatus.GROWING if c == 0 else CropStatus.HARVESTED,
            )
            rows.append(crop)
        for p in range(PURCHASES_PER_FARMER):
            s = seeds[rng.randrange(len(seeds))]
            qty = Decimal(rng.randrange(10, 200))
            rows.append(
                SeedPurchase(
                    farmer_id=f.id, seed_id=s.id, quantity_kg=qty, price_per_kg=s.price_per_kg,
                    total_amount=qty * s.price_per_kg,
                    payment_status=PaymentStatus.PAID if p % 2 else PaymentStatus.PENDING,
                    invoice_number=f"SP-BENCH-{i}-{p}-{uuid.uuid4().hex[:6]}",
                )
            )
        for sidx in range(SALES_PER_FARMER):
            rows.append(
                GrainSale(
                    farmer_id=f.id, grain_type=rng.choice(CROP_TYPES),
                    grade=rng.choice(list(GrainGrade)),
                    raw_material_kg=Decimal(rng.randrange(100, 5000)),
                    good_material_kg=Decimal(rng.randrange(100, 4500)),
                    wastage_kg=Decimal(rng.randrange(0, 200)),
                    price_per_kg=Decimal(rng.randrange(15, 80)),
                    total_amount=Decimal(rng.randrange(1000, 200000)),
                    status=list(GrainSaleStatus)[sidx % len(GrainSaleStatus)],
                )
            )
        for b in range(BOOKINGS_PER_FARMER):
            slot = slots[rng.randrange(len(slots))]
            rows.append(
                BookingSlot(
                    farmer_id=f.id, warehouse_id=slot.warehouse_id, warehouse_slot_id=slot.id,
                    booking_date=slot.slot_date, delivery_address=f"Village {i}",
                    grain_type=rng.choice(CROP_TYPES), quantity_kg=Decimal(rng.randrange(100, 3000)),
                    status=list(BookingStatus)[b % len(BookingStatus)],
                )
            )
        for t in range(TRANSACTIONS_PER_FARMER):
            rows.append(
                Transaction(
                    reference_type=TransactionReferenceType.SEED_PURCHASE
                    if t % 2
                    else TransactionReferenceType.GRAIN_SALE,
                    reference_id=uuid.uuid4(), farmer_id=f.id,
                    amount=Decimal(rng.randrange(500, 90000)),
                    direction=TransactionDirection.DEBIT if t % 2 else TransactionDirection.CREDIT,
                    status=TransactionStatus.COMPLETED if t % 3 else TransactionStatus.PENDING,
                    description="Benchmark transaction",
                )
            )
        for n in range(NOTIFICATIONS_PER_FARMER):
            rows.append(
                Notification(
                    user_id=f.id, title=f"Benchmark notice {n}", message="Benchmark message body",
                    type=NotificationType.INFO, is_read=bool(n % 2),
                )
            )

    db.add_all(rows)
    await db.flush()

    crop_rows = (
        await db.execute(__import__("sqlalchemy").select(Crop).limit(600))
    ).scalars().all()
    visits = []
    for crop in crop_rows:
        for month in (1, 3):
            visits.append(
                FarmVisit(
                    crop_id=crop.id, farmer_id=crop.farmer_id,
                    staff_id=managers[rng.randrange(len(managers))].id,
                    visit_month=month,
                    scheduled_date=today + timedelta(days=rng.randrange(-30, 30)),
                    status=VisitStatus.SCHEDULED,
                )
            )
    db.add_all(visits)

    db.add_all(
        [
            AuditLog(
                user_id=admin.id, action="benchmark.seed", entity_type="benchmark",
                entity_id=uuid.uuid4(), details=f"row {i}",
            )
            for i in range(AUDIT_LOGS)
        ]
    )
    await db.commit()

    active_farmer = next(f for f in farmers if f.status == UserStatus.ACTIVE)
    return {"admin": admin, "manager": managers[0], "farmer": active_farmer}
