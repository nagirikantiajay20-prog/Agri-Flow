"""
Concurrency & Idempotency tests for Seed Purchase financial workflow.
Verifies Issue #6 (Atomic Purchase Idempotency).
"""
import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.exceptions import ConflictError, InsufficientStockError
from app.models.ledger import IdempotencyRecord, Transaction
from app.models.seed import Seed, SeedPurchase, SeedWarehouse
from app.models.warehouse import Warehouse
from app.services import purchase_service
from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sample_seed(db_session, manager):
    wh = Warehouse(
        name="Idempotency Test Warehouse",
        address="Test Address 123",
        location="Hub",
        total_capacity_kg=Decimal("500000.00"),
        is_active=True,
    )
    db_session.add(wh)
    await db_session.flush()

    seed = Seed(
        name="Idempotent Hybrid Seed",
        crop_type="Cotton",
        variety="IH-1",
        price_per_kg=Decimal("120.00"),
        stock_kg=Decimal("100.00"),
        warehouse_id=wh.id,
        is_active=True,
    )
    db_session.add(seed)
    await db_session.flush()

    db_session.add(SeedWarehouse(seed_id=seed.id, warehouse_id=wh.id))
    await db_session.commit()
    return seed, wh


async def test_normal_purchase_creates_records_and_decrements_stock(client, active_farmer, sample_seed, db_session):
    seed, wh = sample_seed
    key = f"idemp-{uuid.uuid4()}"

    resp = await client.post(
        "/api/v1/farmer/seeds/purchase",
        json={
            "seed_id": str(seed.id),
            "quantity_kg": 10.0,
            "warehouse_id": str(wh.id),
            "payment_method": "warehouse",
        },
        headers={**auth_headers(active_farmer), "Idempotency-Key": key},
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["quantity_kg"] == 10.0
    assert data["total_amount"] == 1200.0

    # Verify database state
    await db_session.refresh(seed)
    assert Decimal(str(seed.stock_kg)) == Decimal("90.00")

    # Verify idempotency record exists
    rec = (
        await db_session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == active_farmer.id,
                IdempotencyRecord.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()
    assert rec is not None
    assert rec.response_data["order_id"] == data["order_id"]


async def test_retry_with_same_key_returns_cached_result_without_double_charge(client, active_farmer, sample_seed, db_session):
    seed, wh = sample_seed
    key = f"idemp-{uuid.uuid4()}"
    payload = {
        "seed_id": str(seed.id),
        "quantity_kg": 15.0,
        "warehouse_id": str(wh.id),
        "payment_method": "warehouse",
    }

    # 1. First purchase request
    resp1 = await client.post(
        "/api/v1/farmer/seeds/purchase",
        json=payload,
        headers={**auth_headers(active_farmer), "Idempotency-Key": key},
    )
    assert resp1.status_code == 201
    data1 = resp1.json()["data"]

    # 2. Retry with exact same key and payload
    resp2 = await client.post(
        "/api/v1/farmer/seeds/purchase",
        json=payload,
        headers={**auth_headers(active_farmer), "Idempotency-Key": key},
    )
    assert resp2.status_code == 201
    data2 = resp2.json()["data"]

    # Output MUST match exactly
    assert data1["order_id"] == data2["order_id"]
    assert data1["invoice_number"] == data2["invoice_number"]
    assert data1["total_amount"] == data2["total_amount"]

    # Verify NO SECOND STOCK DECREMENT occurred
    await db_session.refresh(seed)
    # Original 100 - 15 = 85 (NOT 70!)
    assert Decimal(str(seed.stock_kg)) == Decimal("85.00")

    # Verify only ONE SeedPurchase and ONE Transaction in DB
    purchases = (
        await db_session.execute(
            select(SeedPurchase).where(SeedPurchase.farmer_id == active_farmer.id, SeedPurchase.idempotency_key == key)
        )
    ).scalars().all()
    assert len(purchases) == 1

    txs = (
        await db_session.execute(
            select(Transaction).where(Transaction.farmer_id == active_farmer.id, Transaction.reference_id == purchases[0].id)
        )
    ).scalars().all()
    assert len(txs) == 1


async def test_same_key_with_different_payload_is_rejected_with_409(client, active_farmer, sample_seed):
    seed, wh = sample_seed
    key = f"idemp-{uuid.uuid4()}"

    resp1 = await client.post(
        "/api/v1/farmer/seeds/purchase",
        json={
            "seed_id": str(seed.id),
            "quantity_kg": 5.0,
            "warehouse_id": str(wh.id),
            "payment_method": "warehouse",
        },
        headers={**auth_headers(active_farmer), "Idempotency-Key": key},
    )
    assert resp1.status_code == 201

    # Same key but changed quantity to 10.0 kg!
    resp2 = await client.post(
        "/api/v1/farmer/seeds/purchase",
        json={
            "seed_id": str(seed.id),
            "quantity_kg": 10.0,
            "warehouse_id": str(wh.id),
            "payment_method": "warehouse",
        },
        headers={**auth_headers(active_farmer), "Idempotency-Key": key},
    )
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "CONFLICT"


async def test_insufficient_stock_fails_safely_without_persisting_claim(client, active_farmer, sample_seed, db_session):
    seed, wh = sample_seed
    key = f"idemp-{uuid.uuid4()}"

    resp = await client.post(
        "/api/v1/farmer/seeds/purchase",
        json={
            "seed_id": str(seed.id),
            "quantity_kg": 999.0,  # exceeds available 100 kg
            "warehouse_id": str(wh.id),
            "payment_method": "warehouse",
        },
        headers={**auth_headers(active_farmer), "Idempotency-Key": key},
    )
    # Exceeds max_order_quantity_kg or stock
    assert resp.status_code in (409, 422)

    # Database must remain unchanged
    await db_session.refresh(seed)
    assert Decimal(str(seed.stock_kg)) == Decimal("100.00")

    rec = (
        await db_session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == active_farmer.id,
                IdempotencyRecord.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()
    assert rec is None


async def test_concurrent_same_key_requests_result_in_single_purchase(active_farmer, sample_seed):
    """Simulate two concurrent worker tasks sending the same idempotency key at the exact same time."""
    from app.core.database import AsyncSessionLocal

    seed, wh = sample_seed
    key = f"concurrent-{uuid.uuid4()}"

    async def execute_order():
        async with AsyncSessionLocal() as session:
            try:
                order, receipt = await purchase_service.purchase_seeds_idempotent(
                    session,
                    farmer=active_farmer,
                    seed_id=seed.id,
                    quantity_kg=Decimal("10.0"),
                    warehouse_id=wh.id,
                    payment_method="warehouse",
                    upi_id=None,
                    idempotency_key=key,
                )
                await session.commit()
                return receipt
            except Exception as e:
                await session.rollback()
                raise e

    results = await asyncio.gather(execute_order(), execute_order(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 2, "Both concurrent requests should succeed (one creates, one returns cached)"
    # Both must reference the exact same order_id
    assert successes[0]["order_id"] == successes[1]["order_id"]
    assert successes[0]["invoice_number"] == successes[1]["invoice_number"]

    # Verify exactly one purchase recorded in DB
    async with AsyncSessionLocal() as session:
        purchases = (
            await session.execute(
                select(SeedPurchase).where(SeedPurchase.farmer_id == active_farmer.id, SeedPurchase.idempotency_key == key)
            )
        ).scalars().all()
        assert len(purchases) == 1
