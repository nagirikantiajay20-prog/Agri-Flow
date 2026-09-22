"""
Warehouse & Booking service (Master Plan Module 7 — the highest
concurrency-risk module in the system).

`create_booking` ports the verified lock -> check -> create -> increment
-> notify sequence from the legacy `create_booking_slot` RPC (Master Plan
§8/§9). The `SELECT ... FOR UPDATE` on the warehouse_slot row is what
prevents two farmers from both booking the last unit of capacity in the
race described in Master Plan §9/§39 — do not remove it, and do not
replace it with an optimistic check-then-write, which re-opens exactly
that race.

Unit handling: 1 QTL = 100 KG, centralized here (Master Plan §21) rather
than recomputed ad hoc per endpoint, which is what caused the confirmed
production bug fixed in
20260714060000_fix_booking_notification_quintals.sql (Master Plan §1.4).
"""
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    CapacityExceededError,
    ConflictError,
    ForbiddenError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.models.enums import BookingStatus, NotificationType, UserRole
from app.models.user import User
from app.models.warehouse import BookingSlot, Warehouse, WarehouseInventory, WarehouseSlot
from app.schemas.common import PageParams
from app.services import audit_service, notification_service

KG_PER_QUINTAL = Decimal("100")


def kg_to_quintal(kg: Decimal) -> Decimal:
    return (kg / KG_PER_QUINTAL).quantize(Decimal("0.01"))


# Valid booking state transitions (Master Plan §23). Any transition not
# listed here is rejected with a 422, never silently allowed.
_VALID_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED},
    BookingStatus.CONFIRMED: {BookingStatus.DELIVERED, BookingStatus.COMPLETED, BookingStatus.CANCELLED},
    BookingStatus.DELIVERED: {BookingStatus.INSPECTED, BookingStatus.CANCELLED},
    BookingStatus.INSPECTED: {BookingStatus.COMPLETED},
    BookingStatus.COMPLETED: set(),
    BookingStatus.CANCELLED: set(),
}

CAPACITY_RELEASING_STATUSES = {BookingStatus.CANCELLED}


async def list_warehouses(db: AsyncSession) -> list[Warehouse]:
    result = await db.execute(select(Warehouse).where(Warehouse.is_active.is_(True)))
    return list(result.scalars().all())


async def list_slots(db: AsyncSession, *, warehouse_id: uuid.UUID) -> list[WarehouseSlot]:
    result = await db.execute(
        select(WarehouseSlot)
        .where(WarehouseSlot.warehouse_id == warehouse_id, WarehouseSlot.status == "active")
        .order_by(WarehouseSlot.slot_date, WarehouseSlot.start_time)
    )
    return list(result.scalars().all())


async def create_booking(
    db: AsyncSession,
    *,
    farmer: User,
    warehouse_id: uuid.UUID,
    warehouse_slot_id: uuid.UUID,
    booking_date,
    delivery_address: str,
    grain_type: str,
    quantity_kg: Decimal,
    grain_sale_id: uuid.UUID | None,
    notes: str | None,
) -> BookingSlot:
    # 1. Lock the warehouse and warehouse slot rows — critical section for concurrency.
    wh_res = await db.execute(
        select(Warehouse).where(Warehouse.id == warehouse_id).with_for_update()
    )
    warehouse = wh_res.scalar_one_or_none()
    if warehouse is None:
        raise NotFoundError("Warehouse not found")

    wh_avail = Decimal(warehouse.total_capacity_kg) - Decimal(warehouse.current_load_kg or 0)
    if quantity_kg > wh_avail:
        raise CapacityExceededError(
            f"Warehouse overall capacity exceeded. Only {wh_avail} kg available in warehouse.",
            details={"total_capacity_kg": str(warehouse.total_capacity_kg), "current_load_kg": str(warehouse.current_load_kg), "requested_kg": str(quantity_kg)},
        )

    result = await db.execute(
        select(WarehouseSlot).where(WarehouseSlot.id == warehouse_slot_id).with_for_update()
    )
    slot = result.scalar_one_or_none()
    if slot is None or slot.warehouse_id != warehouse_id:
        raise NotFoundError("Warehouse slot not found")
    if slot.status != "active":
        raise ConflictError("This slot is no longer accepting bookings")
    from datetime import date as _date

    if slot.slot_date < _date.today():
        raise ConflictError("This slot is in the past and can no longer be booked")
    if booking_date != slot.slot_date:
        from app.core.exceptions import ValidationError

        raise ValidationError(
            "booking_date must match the selected slot's date",
            details={"slot_date": str(slot.slot_date), "booking_date": str(booking_date)},
        )

    # 2. Check the slot's booking-count limit, then its weight capacity.
    if slot.current_booking_count >= slot.max_bookings:
        raise CapacityExceededError(
            "This time slot has reached its booking limit — please choose another slot",
            details={"max_bookings": slot.max_bookings},
        )
    remaining = slot.capacity_kg - slot.booked_kg
    if quantity_kg > remaining:
        raise CapacityExceededError(
            f"Only {remaining} kg remaining in this slot",
            details={"remaining_kg": str(remaining), "requested_kg": str(quantity_kg)},
        )

    # 3. Prevent duplicate booking: same farmer, same slot, still pending/confirmed.
    dup = await db.execute(
        select(BookingSlot).where(
            BookingSlot.farmer_id == farmer.id,
            BookingSlot.warehouse_slot_id == warehouse_slot_id,
            BookingSlot.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
        )
    )
    if dup.scalar_one_or_none() is not None:
        raise ConflictError("You already have an active booking for this slot")

    # 4. Create booking.
    booking = BookingSlot(
        farmer_id=farmer.id,
        grain_sale_id=grain_sale_id,
        warehouse_id=warehouse_id,
        warehouse_slot_id=warehouse_slot_id,
        booking_date=booking_date,
        delivery_address=delivery_address,
        grain_type=grain_type,
        quantity_kg=quantity_kg,
        notes=notes,
    )
    db.add(booking)

    # 5. Increment capacity atomically on both slot and warehouse.
    slot.booked_kg += quantity_kg
    slot.current_booking_count += 1
    warehouse.current_load_kg = Decimal(warehouse.current_load_kg or 0) + quantity_kg

    await db.flush()

    # 6. Notify (unit-consistent: always kg internally, quintal shown as a
    #    display conversion at the presentation layer, not recomputed here
    #    — this exact ambiguity is what caused the confirmed production bug
    #    in Master Plan §1.4).
    await notification_service.notify_user(
        db,
        user_id=farmer.id,
        title="Booking submitted",
        message=f"Your booking of {quantity_kg} kg ({kg_to_quintal(quantity_kg)} qtl) is pending confirmation.",
        type_=NotificationType.INFO,
        reference_type="booking_slot",
        reference_id=booking.id,
    )
    await notification_service.notify_roles(
        db,
        roles=[UserRole.MANAGER, UserRole.SUPER_ADMIN],
        title="New warehouse booking",
        message=f"{farmer.name} booked {quantity_kg} kg at a warehouse slot.",
        reference_type="booking_slot",
        reference_id=booking.id,
    )

    # 7. Audit.
    await audit_service.record(
        db,
        actor_id=farmer.id,
        action="booking.create",
        entity_type="booking_slot",
        entity_id=booking.id,
        new_value={"quantity_kg": str(quantity_kg), "warehouse_slot_id": str(warehouse_slot_id)},
    )
    return booking


async def list_bookings_for_actor(db: AsyncSession, *, actor: User, params: PageParams) -> tuple[list[BookingSlot], int]:
    query = select(BookingSlot)
    count_query = select(func.count()).select_from(BookingSlot)
    if actor.role.value == "farmer":
        query = query.where(BookingSlot.farmer_id == actor.id)
        count_query = count_query.where(BookingSlot.farmer_id == actor.id)
    if params.status:
        query = query.where(BookingSlot.status == params.status)
        count_query = count_query.where(BookingSlot.status == params.status)

    total = (await db.execute(count_query)).scalar_one()
    query = (
        query.order_by(BookingSlot.created_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def update_booking_status(
    db: AsyncSession, *, actor: User, booking_id: uuid.UUID, new_status: BookingStatus, notes: str | None
) -> BookingSlot:
    # Lock the booking row (and its slot) for the duration of this
    # transition — required when the transition also needs to release
    # reserved capacity (cancellation) without racing a concurrent booking.
    result = await db.execute(select(BookingSlot).where(BookingSlot.id == booking_id).with_for_update())
    booking = result.scalar_one_or_none()
    if booking is None:
        raise NotFoundError("Booking not found")

    # A farmer reaches this endpoint only via the `booking.cancel.own`
    # permission (never `booking.review`) — restrict what they're
    # allowed to do here to cancelling their OWN booking. Everything
    # else (confirm, complete, cancel someone else's booking) requires
    # `booking.review`, i.e. manager/super_admin. This is the fix for
    # the "farmer can't cancel their own booking" gap noted in the
    # backend README's Known Simplifications.
    if actor.role == UserRole.FARMER:
        if booking.farmer_id != actor.id:
            raise ForbiddenError("You can only cancel your own bookings")
        if new_status != BookingStatus.CANCELLED:
            raise ForbiddenError("Farmers may only cancel a booking, not change it to any other status")

    allowed_next = _VALID_TRANSITIONS.get(booking.status, set())
    if new_status not in allowed_next:
        raise InvalidStateTransitionError(
            f"Cannot move booking from {booking.status.value} to {new_status.value}",
            details={"from": booking.status.value, "to": new_status.value},
        )

    old_status = booking.status
    booking.status = new_status
    if notes:
        booking.notes = notes

    # Cancelling releases reserved capacity back to the slot and warehouse.
    if new_status == BookingStatus.CANCELLED:
      if booking.warehouse_slot_id:
        slot_result = await db.execute(
            select(WarehouseSlot).where(WarehouseSlot.id == booking.warehouse_slot_id).with_for_update()
        )
        slot = slot_result.scalar_one_or_none()
        if slot:
            slot.booked_kg = max(Decimal("0"), slot.booked_kg - booking.quantity_kg)
            slot.current_booking_count = max(0, slot.current_booking_count - 1)
      if booking.warehouse_id:
        wh_result = await db.execute(
            select(Warehouse).where(Warehouse.id == booking.warehouse_id).with_for_update()
        )
        wh = wh_result.scalar_one_or_none()
        if wh:
            wh.current_load_kg = max(Decimal("0"), Decimal(wh.current_load_kg or 0) - Decimal(booking.quantity_kg))

    await notification_service.notify_user(
        db,
        user_id=booking.farmer_id,
        title="Booking status updated",
        message=f"Your booking is now {new_status.value}.",
        type_=NotificationType.SUCCESS if new_status == BookingStatus.CONFIRMED else NotificationType.INFO,
        reference_type="booking_slot",
        reference_id=booking.id,
    )
    await audit_service.record(
        db,
        actor_id=actor.id,
        action="booking.status_update",
        entity_type="booking_slot",
        entity_id=booking.id,
        old_value={"status": old_status.value},
        new_value={"status": new_status.value},
    )
    await db.flush()
    return booking


SLOT_LIST_MAX_ROWS = 500


async def list_slots_filtered(
    db: AsyncSession,
    *,
    warehouse_id: uuid.UUID | None = None,
    slot_date=None,
    include_inactive: bool = False,
) -> list[WarehouseSlot]:
    from datetime import date as _date

    query = select(WarehouseSlot)
    if warehouse_id is not None:
        query = query.where(WarehouseSlot.warehouse_id == warehouse_id)
    if slot_date is not None:
        query = query.where(WarehouseSlot.slot_date == slot_date)
    elif warehouse_id is None:
        # Unqualified listing would scan every slot ever created. Nobody
        # books into the past, so bound it to the forward-looking window
        # the caller can actually act on.
        query = query.where(WarehouseSlot.slot_date >= _date.today())
    if not include_inactive:
        query = query.where(WarehouseSlot.status == "active")
    query = query.order_by(WarehouseSlot.slot_date.desc(), WarehouseSlot.start_time).limit(
        SLOT_LIST_MAX_ROWS
    )
    return list((await db.execute(query)).scalars().all())


async def create_warehouse(
    db: AsyncSession, *, actor: User, name: str, address: str, total_capacity_kg: Decimal,
    manager_id: uuid.UUID | None = None,
) -> Warehouse:
    warehouse = Warehouse(
        name=name, address=address, total_capacity_kg=total_capacity_kg, manager_id=manager_id
    )
    db.add(warehouse)
    await db.flush()
    await audit_service.record(
        db, actor_id=actor.id, action="warehouse.create", entity_type="warehouse", entity_id=warehouse.id,
        new_value={"name": name, "total_capacity_kg": str(total_capacity_kg)},
    )
    return warehouse


async def add_inventory(
    db: AsyncSession, *, actor: User, warehouse_id: uuid.UUID, grain_type: str, quantity_kg: Decimal
) -> WarehouseInventory:
    result = await db.execute(
        select(Warehouse).where(Warehouse.id == warehouse_id).with_for_update()
    )
    warehouse = result.scalar_one_or_none()
    if warehouse is None:
        raise NotFoundError("Warehouse not found")

    if warehouse.current_load_kg + quantity_kg > warehouse.total_capacity_kg:
        raise CapacityExceededError(
            "Adding this quantity would exceed the warehouse's total capacity",
            details={
                "capacity_kg": str(warehouse.total_capacity_kg),
                "current_load_kg": str(warehouse.current_load_kg),
                "requested_kg": str(quantity_kg),
            },
        )

    row_result = await db.execute(
        select(WarehouseInventory)
        .where(
            WarehouseInventory.warehouse_id == warehouse_id,
            WarehouseInventory.grain_type == grain_type,
        )
        .with_for_update()
    )
    row = row_result.scalar_one_or_none()
    if row is None:
        row = WarehouseInventory(warehouse_id=warehouse_id, grain_type=grain_type, quantity_kg=quantity_kg)
        db.add(row)
    else:
        row.quantity_kg += quantity_kg

    warehouse.current_load_kg += quantity_kg
    await db.flush()
    await audit_service.record(
        db, actor_id=actor.id, action="warehouse.inventory_add", entity_type="warehouse",
        entity_id=warehouse_id, new_value={"grain_type": grain_type, "quantity_kg": str(quantity_kg)},
    )
    return row


async def create_slot(
    db: AsyncSession, *, actor: User, warehouse_id: uuid.UUID, slot_date, start_time, end_time,
    capacity_kg: Decimal,
) -> WarehouseSlot:
    warehouse = await db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise NotFoundError("Warehouse not found")
    if end_time <= start_time:
        raise ConflictError("Slot end time must be after its start time")

    slot = WarehouseSlot(
        warehouse_id=warehouse_id, slot_date=slot_date, start_time=start_time,
        end_time=end_time, capacity_kg=capacity_kg,
    )
    db.add(slot)
    await db.flush()
    await audit_service.record(
        db, actor_id=actor.id, action="warehouse_slot.create", entity_type="warehouse_slot",
        entity_id=slot.id, new_value={"slot_date": str(slot_date), "capacity_kg": str(capacity_kg)},
    )
    return slot


async def update_slot(
    db: AsyncSession, *, actor: User, slot_id: uuid.UUID, status: str | None, capacity_kg: Decimal | None
) -> WarehouseSlot:
    result = await db.execute(select(WarehouseSlot).where(WarehouseSlot.id == slot_id).with_for_update())
    slot = result.scalar_one_or_none()
    if slot is None:
        raise NotFoundError("Warehouse slot not found")

    old = {"status": slot.status, "capacity_kg": str(slot.capacity_kg)}
    if status is not None:
        if status not in ("active", "cancelled"):
            raise ConflictError("Slot status must be 'active' or 'cancelled'")
        slot.status = status
    if capacity_kg is not None:
        if capacity_kg < slot.booked_kg:
            raise CapacityExceededError(
                f"Cannot shrink capacity below the {slot.booked_kg} kg already booked",
                details={"booked_kg": str(slot.booked_kg), "requested_capacity_kg": str(capacity_kg)},
            )
        slot.capacity_kg = capacity_kg

    await audit_service.record(
        db, actor_id=actor.id, action="warehouse_slot.update", entity_type="warehouse_slot",
        entity_id=slot.id, old_value=old,
        new_value={"status": slot.status, "capacity_kg": str(slot.capacity_kg)},
    )
    await db.flush()
    return slot
