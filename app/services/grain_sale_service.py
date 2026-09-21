"""
Grain Sales & Procurement Inspection service (Master Plan Module 8).

Ports the verified sequence from `procure_grain_booking` / `inspect_crop`
/ `review_grain_sale` / `pay_grain_sale`: lock booking -> validate
quantity -> record quality inspection -> calculate amount from
market_rates -> update grain_sales -> ledger -> notify -> audit.

The amount is pinned at review time onto the grain_sales row
(price_per_kg / total_amount) so a later market_rates change never
retroactively alters an already-reviewed sale (Master Plan Module 8).

State machine (Master Plan §23): pending -> approved -> paid, or
pending -> rejected. No other transition is permitted.
"""
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.enums import (
    GrainSaleStatus,
    NotificationType,
    TransactionDirection,
    TransactionReferenceType,
    TransactionStatus,
    UserRole,
)
from app.models.grain import CropInspection, GrainSale
from app.models.ledger import Transaction
from app.models.user import User
from app.models.warehouse import BookingSlot
from app.schemas.common import PageParams
from app.services import audit_service, market_rate_service, notification_service

_VALID_TRANSITIONS: dict[GrainSaleStatus, set[GrainSaleStatus]] = {
    GrainSaleStatus.PENDING: {GrainSaleStatus.RECEIVED, GrainSaleStatus.APPROVED, GrainSaleStatus.REJECTED},
    GrainSaleStatus.RECEIVED: {GrainSaleStatus.APPROVED, GrainSaleStatus.REJECTED},
    GrainSaleStatus.APPROVED: {GrainSaleStatus.PAID},
    GrainSaleStatus.REJECTED: set(),
    GrainSaleStatus.PAID: set(),
}


async def create_grain_sale(
    db: AsyncSession,
    *,
    farmer: User,
    grain_type: str,
    grade,
    raw_material_kg: Decimal,
    crop_id: uuid.UUID | None,
    offered_price_per_kg: Decimal | None = None,
    notes: str | None = None,
) -> GrainSale:
    if crop_id is not None:
        from app.models.crop import Crop

        owned = await db.execute(select(Crop.id).where(Crop.id == crop_id, Crop.farmer_id == farmer.id))
        if owned.scalar_one_or_none() is None:
            raise NotFoundError("Crop not found")

    sale = GrainSale(
        farmer_id=farmer.id,
        crop_id=crop_id,
        grain_type=grain_type,
        grade=grade,
        raw_material_kg=raw_material_kg,
        offered_price_per_kg=offered_price_per_kg,
        notes=notes,
        status=GrainSaleStatus.PENDING,
    )
    db.add(sale)
    await db.flush()
    await audit_service.record(db, actor_id=farmer.id, action="grain_sale.create", entity_type="grain_sale", entity_id=sale.id)
    return sale


async def list_grain_sales_for_actor(db: AsyncSession, *, actor: User, params: PageParams) -> tuple[list[GrainSale], int]:
    query = select(GrainSale)
    count_query = select(func.count()).select_from(GrainSale)
    if actor.role.value == "farmer":
        query = query.where(GrainSale.farmer_id == actor.id)
        count_query = count_query.where(GrainSale.farmer_id == actor.id)
    if params.status:
        query = query.where(GrainSale.status == params.status)
        count_query = count_query.where(GrainSale.status == params.status)

    total = (await db.execute(count_query)).scalar_one()
    query = query.order_by(GrainSale.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def review_grain_sale(
    db: AsyncSession,
    *,
    manager: User,
    sale_id: uuid.UUID,
    booking_slot_id: uuid.UUID,
    good_quantity_kg: Decimal,
    bad_quantity_kg: Decimal,
    rejection_reason: str | None,
    approve: bool,
) -> GrainSale:
    # Lock the grain sale row for the duration of the review.
    result = await db.execute(select(GrainSale).where(GrainSale.id == sale_id).with_for_update())
    sale = result.scalar_one_or_none()
    if sale is None:
        raise NotFoundError("Grain sale not found")

    target_status = GrainSaleStatus.APPROVED if approve else GrainSaleStatus.REJECTED
    allowed = _VALID_TRANSITIONS.get(sale.status, set())
    if target_status not in allowed:
        raise InvalidStateTransitionError(
            f"Cannot move grain sale from {sale.status.value} to {target_status.value}",
            details={"from": sale.status.value, "to": target_status.value},
        )

    booking = await db.get(BookingSlot, booking_slot_id)
    if booking is None:
        raise NotFoundError("Booking slot not found")

    # Record the quality inspection (Master Plan §1.6 correction: this is
    # the `crop_inspections` table — grain quality at procurement time,
    # not a farm-visit crop-health inspection).
    inspection = CropInspection(
        booking_slot_id=booking_slot_id,
        grain_sale_id=sale.id,
        inspector_id=manager.id,
        good_quantity_kg=good_quantity_kg,
        bad_quantity_kg=bad_quantity_kg,
        rejection_reason=rejection_reason,
    )
    db.add(inspection)

    sale.wastage_kg = bad_quantity_kg
    sale.good_material_kg = good_quantity_kg
    sale.status = target_status

    if approve:
        # Pin the rate at approval time — look up today's market rate for
        # this grain_type/grade and freeze it onto the sale row so a later
        # rate change never retroactively alters a completed sale.
        price_per_kg = await market_rate_service.resolve_price_per_kg(
            db, crop_type=sale.grain_type, grade=sale.grade
        )
        sale.price_per_kg = price_per_kg
        sale.total_amount = (price_per_kg * good_quantity_kg).quantize(Decimal("0.01"))

    await audit_service.record(
        db,
        actor_id=manager.id,
        action="grain_sale.review",
        entity_type="grain_sale",
        entity_id=sale.id,
        new_value={"status": sale.status.value, "good_material_kg": str(good_quantity_kg)},
    )
    await notification_service.notify_user(
        db,
        user_id=sale.farmer_id,
        title="Grain sale reviewed",
        message=f"Your grain sale was {sale.status.value}."
        + (f" Amount: ₹{sale.total_amount}" if approve else ""),
        type_=NotificationType.SUCCESS if approve else NotificationType.WARNING,
        reference_type="grain_sale",
        reference_id=sale.id,
    )
    await db.flush()
    return sale


async def pay_grain_sale(db: AsyncSession, *, manager: User, sale_id: uuid.UUID, payment_reference: str | None) -> GrainSale:
    result = await db.execute(select(GrainSale).where(GrainSale.id == sale_id).with_for_update())
    sale = result.scalar_one_or_none()
    if sale is None:
        raise NotFoundError("Grain sale not found")

    allowed = _VALID_TRANSITIONS.get(sale.status, set())
    if GrainSaleStatus.PAID not in allowed:
        raise InvalidStateTransitionError(
            f"Cannot pay a grain sale in status {sale.status.value}",
            details={"from": sale.status.value, "to": GrainSaleStatus.PAID.value},
        )

    sale.status = GrainSaleStatus.PAID

    db.add(
        Transaction(
            reference_type=TransactionReferenceType.GRAIN_SALE,
            reference_id=sale.id,
            farmer_id=sale.farmer_id,
            amount=sale.total_amount,
            direction=TransactionDirection.CREDIT,
            status=TransactionStatus.COMPLETED,
            description=f"Grain sale payment: {sale.grain_type} ({sale.good_material_kg} kg)",
            transaction_id=payment_reference,
        )
    )

    await notification_service.notify_user(
        db,
        user_id=sale.farmer_id,
        title="Payment released",
        message=f"₹{sale.total_amount} has been credited for your grain sale.",
        type_=NotificationType.SUCCESS,
        reference_type="grain_sale",
        reference_id=sale.id,
    )
    await audit_service.record(
        db, actor_id=manager.id, action="grain_sale.pay", entity_type="grain_sale", entity_id=sale.id,
        new_value={"amount": str(sale.total_amount)},
    )
    await db.flush()
    return sale


async def procure_grain(
    db: AsyncSession,
    *,
    manager: User,
    farmer_id: uuid.UUID,
    grain_type: str,
    grade,
    raw_material_kg: Decimal,
    good_material_kg: Decimal,
    wastage_kg: Decimal,
) -> GrainSale:
    from app.core.exceptions import ValidationError
    from app.models.user import User as UserModel

    farmer = await db.get(UserModel, farmer_id)
    if farmer is None or farmer.role != UserRole.FARMER:
        raise NotFoundError("Farmer not found")
    if abs((good_material_kg + wastage_kg) - raw_material_kg) > Decimal("0.01"):
        raise ValidationError(
            "good_material_kg + wastage_kg must equal raw_material_kg",
            details={
                "raw_material_kg": str(raw_material_kg),
                "good_material_kg": str(good_material_kg),
                "wastage_kg": str(wastage_kg),
            },
        )

    price_per_kg = await market_rate_service.resolve_price_per_kg(db, crop_type=grain_type, grade=grade)
    sale = GrainSale(
        farmer_id=farmer_id,
        grain_type=grain_type,
        grade=grade,
        raw_material_kg=raw_material_kg,
        good_material_kg=good_material_kg,
        wastage_kg=wastage_kg,
        price_per_kg=price_per_kg,
        total_amount=(price_per_kg * good_material_kg).quantize(Decimal("0.01")),
        status=GrainSaleStatus.RECEIVED,
    )
    db.add(sale)
    await db.flush()

    await notification_service.notify_roles(
        db,
        roles=[UserRole.SUPER_ADMIN],
        title="Crop procured",
        message=f"{good_material_kg} kg of {grain_type} procured from {farmer.name} - ready for payment.",
        type_=NotificationType.INFO,
        reference_type="grain_sale",
        reference_id=sale.id,
    )
    await audit_service.record(
        db, actor_id=manager.id, action="grain_sale.procure", entity_type="grain_sale", entity_id=sale.id,
        new_value={"good_material_kg": str(good_material_kg), "total_amount": str(sale.total_amount)},
    )
    await db.flush()
    return sale


async def inspect_booking(
    db: AsyncSession,
    *,
    inspector: User,
    booking_id: uuid.UUID,
    good_quantity_kg: Decimal,
    bad_quantity_kg: Decimal,
    rejection_reason: str | None,
    notes: str | None,
) -> CropInspection:
    from app.core.exceptions import ConflictError, ValidationError
    from app.models.enums import BookingStatus

    result = await db.execute(select(BookingSlot).where(BookingSlot.id == booking_id).with_for_update())
    booking = result.scalar_one_or_none()
    if booking is None:
        raise NotFoundError("Booking not found")
    if booking.status != BookingStatus.DELIVERED:
        raise InvalidStateTransitionError(
            f"Inspection requires a delivered booking, but this one is {booking.status.value}",
            details={"from": booking.status.value, "expected": BookingStatus.DELIVERED.value},
        )
    if abs((good_quantity_kg + bad_quantity_kg) - booking.quantity_kg) > Decimal("0.01"):
        raise ValidationError(
            "good_quantity_kg + bad_quantity_kg must equal the booked quantity",
            details={
                "booked_kg": str(booking.quantity_kg),
                "good_quantity_kg": str(good_quantity_kg),
                "bad_quantity_kg": str(bad_quantity_kg),
            },
        )

    existing = await db.execute(select(CropInspection).where(CropInspection.booking_slot_id == booking_id))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("This booking has already been inspected")
    if booking.grain_sale_id is None:
        raise ConflictError("No grain sale is linked to this booking")

    sale_result = await db.execute(
        select(GrainSale).where(GrainSale.id == booking.grain_sale_id).with_for_update()
    )
    sale = sale_result.scalar_one_or_none()
    if sale is None:
        raise NotFoundError("Linked grain sale not found")
    if GrainSaleStatus.RECEIVED not in _VALID_TRANSITIONS.get(sale.status, set()):
        raise InvalidStateTransitionError(
            f"Cannot record an inspection against a grain sale in status {sale.status.value}",
            details={"from": sale.status.value, "to": GrainSaleStatus.RECEIVED.value},
        )

    inspection = CropInspection(
        booking_slot_id=booking_id,
        grain_sale_id=sale.id,
        inspector_id=inspector.id,
        good_quantity_kg=good_quantity_kg,
        bad_quantity_kg=bad_quantity_kg,
        rejection_reason=rejection_reason,
        notes=notes,
    )
    db.add(inspection)

    price_per_kg = await market_rate_service.resolve_price_per_kg(
        db, crop_type=sale.grain_type, grade=sale.grade
    )
    sale.raw_material_kg = booking.quantity_kg
    sale.good_material_kg = good_quantity_kg
    sale.wastage_kg = bad_quantity_kg
    sale.price_per_kg = price_per_kg
    sale.total_amount = (price_per_kg * good_quantity_kg).quantize(Decimal("0.01"))
    sale.status = GrainSaleStatus.RECEIVED
    booking.status = BookingStatus.INSPECTED

    await notification_service.notify_user(
        db,
        user_id=sale.farmer_id,
        title="Delivery inspected",
        message=f"Inspection complete: {good_quantity_kg} kg accepted, {bad_quantity_kg} kg rejected.",
        type_=NotificationType.INFO,
        reference_type="grain_sale",
        reference_id=sale.id,
    )
    await audit_service.record(
        db, actor_id=inspector.id, action="grain_sale.inspect", entity_type="grain_sale", entity_id=sale.id,
        new_value={"good_quantity_kg": str(good_quantity_kg), "bad_quantity_kg": str(bad_quantity_kg)},
    )
    await db.flush()
    return inspection


async def update_yield(
    db: AsyncSession,
    *,
    manager: User,
    sale_id: uuid.UUID,
    good_material_kg: Decimal,
    wastage_kg: Decimal,
) -> GrainSale:
    from app.core.exceptions import ConflictError

    result = await db.execute(select(GrainSale).where(GrainSale.id == sale_id).with_for_update())
    sale = result.scalar_one_or_none()
    if sale is None:
        raise NotFoundError("Grain sale not found")
    if sale.status in (GrainSaleStatus.PAID, GrainSaleStatus.REJECTED):
        raise ConflictError(f"Cannot adjust the yield of a {sale.status.value} grain sale")

    old = {"good_material_kg": str(sale.good_material_kg), "wastage_kg": str(sale.wastage_kg)}
    price_per_kg = await market_rate_service.resolve_price_per_kg(
        db, crop_type=sale.grain_type, grade=sale.grade
    )
    sale.good_material_kg = good_material_kg
    sale.wastage_kg = wastage_kg
    sale.raw_material_kg = good_material_kg + wastage_kg
    sale.price_per_kg = price_per_kg
    sale.total_amount = (price_per_kg * good_material_kg).quantize(Decimal("0.01"))
    if sale.status == GrainSaleStatus.PENDING:
        sale.status = GrainSaleStatus.RECEIVED

    await notification_service.notify_roles(
        db,
        roles=[UserRole.SUPER_ADMIN],
        title="Procurement yield updated",
        message=f"Yield for {sale.grain_type} updated to {good_material_kg} kg good / {wastage_kg} kg waste.",
        type_=NotificationType.INFO,
        reference_type="grain_sale",
        reference_id=sale.id,
    )
    await audit_service.record(
        db, actor_id=manager.id, action="grain_sale.yield_update", entity_type="grain_sale",
        entity_id=sale.id, old_value=old,
        new_value={"good_material_kg": str(good_material_kg), "wastage_kg": str(wastage_kg)},
    )
    await db.flush()
    return sale
