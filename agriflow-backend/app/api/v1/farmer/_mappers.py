from __future__ import annotations

from decimal import Decimal

from app.integrations import storage
from app.models.crop import Crop, FarmVisit
from app.models.enums import BankRequestStatus
from app.models.grain import GrainSale
from app.models.ledger import BankChangeRequest
from app.models.seed import Seed
from app.models.user import FarmerProfile, User
from app.models.warehouse import BookingSlot, Warehouse, WarehouseSlot
from app.schemas import farmer_app as s
from app.schemas.common import PaginatedResponse, Pagination

KG_PER_QUINTAL = Decimal("100")


def profile_out(
    user: User,
    profile: FarmerProfile | None,
    latest_bank_request: BankChangeRequest | None = None,
) -> s.FarmerProfileOut:
    p = profile
    bank_st = "approved"
    if p and p.bank_status:
        bank_st = p.bank_status.value if hasattr(p.bank_status, "value") else str(p.bank_status)

    pending_req: s.PendingBankRequestOut | None = None
    if latest_bank_request and latest_bank_request.status in (BankRequestStatus.PENDING, BankRequestStatus.REJECTED):
        status_str = (
            latest_bank_request.status.value
            if hasattr(latest_bank_request.status, "value")
            else str(latest_bank_request.status)
        )
        pending_req = s.PendingBankRequestOut(
            request_id=latest_bank_request.id,
            bank_name=latest_bank_request.bank_name,
            masked_account_number=s.mask_account(latest_bank_request.account_number),
            ifsc_code=latest_bank_request.ifsc_code,
            upi_id=latest_bank_request.upi_id,
            status=status_str,
            requested_at=latest_bank_request.requested_at,
            admin_notes=latest_bank_request.admin_notes,
        )

    return s.FarmerProfileOut(
        farmer_id=user.id,
        name=user.name,
        phone=user.phone,
        email=user.email,
        status=user.status,
        farm_name=p.farm_name if p else None,
        address=p.address if p else None,
        village=p.village if p else None,
        district=p.district if p else None,
        state=p.state if p else None,
        crop_address=p.crop_address if p else None,
        acres_of_land=Decimal(p.acres_of_land or 0) if p else Decimal("0"),
        soil_type=p.soil_type if p else None,
        irrigation_type=p.irrigation_type if p else None,
        primary_crop=p.primary_crop if p else None,
        secondary_crop=p.secondary_crop if p else None,
        bank_name=p.bank_name if p else None,
        bank_account_number=s.mask_account(p.account_number) if p else None,
        bank_ifsc=p.ifsc_code if p else None,
        upi_id=p.upi_id if p else None,
        bank_status=bank_st,
        avatar_url=storage.read_url(p.profile_photo) if p else None,
        aadhaar_url=storage.read_url(p.aadhaar_card_url) if p else None,
        passbook_url=storage.read_url(p.bank_passbook_url) if p else None,
        land_proof_url=storage.read_url(p.land_ownership_url) if p else None,
        pending_bank_request=pending_req,
    )


def seed_out(seed: Seed) -> s.SeedOut:
    return s.SeedOut(
        id=seed.id,
        name=seed.name,
        crop_type=seed.crop_type,
        variety=seed.variety,
        description=seed.description,
        price_per_kg=Decimal(seed.price_per_kg),
        price_grade_a=Decimal(seed.price_grade_a) if getattr(seed, "price_grade_a", None) is not None else None,
        price_grade_b=Decimal(seed.price_grade_b) if getattr(seed, "price_grade_b", None) is not None else None,
        price_grade_c=Decimal(seed.price_grade_c) if getattr(seed, "price_grade_c", None) is not None else None,
        max_order_quantity_kg=Decimal(seed.max_order_quantity_kg) if getattr(seed, "max_order_quantity_kg", None) is not None else None,
        old_price=Decimal(seed.old_price) if seed.old_price is not None else None,
        stock_kg=Decimal(seed.stock_kg),
        image_url=storage.read_url(seed.image_url),
        warehouse_id=seed.warehouse_id,
    )


def crop_out(crop: Crop) -> s.CropOut:
    life_st = crop.status.value if hasattr(crop.status, "value") else str(crop.status)
    return s.CropOut(
        id=crop.id,
        farmer_id=crop.farmer_id,
        crop_name=crop.crop_name,
        crop_type=crop.crop_type,
        acres=Decimal(crop.acres),
        sowing_date=crop.sowing_date,
        harvest_date=crop.harvest_date,
        status=crop.stage,
        lifecycle_status=life_st,
        notes=crop.notes,
        created_at=crop.created_at,
        deleted_at=crop.deleted_at,
        is_deleted=crop.deleted_at is not None,
    )


def visit_out(visit: FarmVisit) -> s.VisitOut:
    return s.VisitOut(
        id=visit.id,
        crop_id=visit.crop_id,
        visit_month=visit.visit_month,
        visit_date=visit.actual_date or visit.scheduled_date,
        scheduled_date=visit.scheduled_date,
        status=visit.status,
        notes=visit.notes,
        report=visit.report,
        diagnosis=visit.diagnosis,
        recommendation=visit.recommendation,
        image_url=storage.read_url(visit.image_path),
    )


def warehouse_out(w: Warehouse) -> s.WarehouseOut:
    total = Decimal(w.total_capacity_kg)
    used = Decimal(w.current_load_kg or 0)
    avail = max(Decimal("0"), total - used)
    ratio = avail / total if total > 0 else Decimal("0")
    if avail <= 0:
        status_str = "Full"
    elif ratio < Decimal("0.4") or avail < Decimal("100000"):
        status_str = "Moderate"
    else:
        status_str = "Available"

    return s.WarehouseOut(
        id=w.id,
        name=w.name,
        address=w.address,
        location=w.location,
        contact_number=w.contact_number,
        capacity=total,
        available_capacity=avail,
        total_capacity_kg=total,
        current_load_kg=used,
        status=status_str,
    )


def slot_time(slot: WarehouseSlot | None) -> str | None:
    if slot is None:
        return None
    return f"{slot.start_time:%I:%M %p} - {slot.end_time:%I:%M %p}"


def slot_out(slot: WarehouseSlot) -> s.SlotOut:
    available = max(Decimal("0"), Decimal(slot.capacity_kg) - Decimal(slot.booked_kg))
    return s.SlotOut(
        id=slot.id,
        warehouse_id=slot.warehouse_id,
        slot_date=slot.slot_date,
        slot_time=slot_time(slot),
        start_time=slot.start_time,
        end_time=slot.end_time,
        total_capacity_kg=Decimal(slot.capacity_kg),
        available_weight_kg=available,
        available_weight_qtl=(available / KG_PER_QUINTAL).quantize(Decimal("0.01")),
        max_bookings=slot.max_bookings,
        available_bookings=max(0, slot.max_bookings - slot.current_booking_count),
        status=slot.status,
    )


def booking_out(b: BookingSlot, warehouse: Warehouse | None, slot: WarehouseSlot | None) -> s.BookingOut:
    return s.BookingOut(
        id=b.id,
        grain_type=b.grain_type,
        quantity_kg=Decimal(b.quantity_kg),
        booking_date=b.booking_date,
        delivery_address=b.delivery_address,
        status=b.status,
        notes=b.notes,
        warehouse_slot_id=b.warehouse_slot_id,
        slot_time=slot_time(slot),
        created_at=b.created_at,
        warehouse=s.WarehouseBrief(
            id=warehouse.id, name=warehouse.name, address=warehouse.address,
            contact_number=warehouse.contact_number,
        )
        if warehouse
        else None,
    )


def offer_out(sale: GrainSale) -> s.GrainOfferOut:
    sale_st = sale.status.value if hasattr(sale.status, "value") else str(sale.status)
    return s.GrainOfferOut(
        id=sale.id,
        crop_type=sale.grain_type,
        grade=sale.grade,
        quantity_kg=Decimal(sale.raw_material_kg),
        offered_price_per_kg=Decimal(sale.offered_price_per_kg) if sale.offered_price_per_kg is not None else None,
        price_per_kg=Decimal(sale.price_per_kg) if sale.price_per_kg is not None else None,
        good_material_kg=Decimal(sale.good_material_kg),
        wastage_kg=Decimal(sale.wastage_kg),
        total_amount=Decimal(sale.total_amount),
        status=sale_st,
        notes=sale.notes,
        created_at=sale.created_at,
    )


def rate_out(rate: dict) -> s.MarketRateOut:
    return s.MarketRateOut(
        crop_type=rate["crop_type"],
        grade=rate["grade"],
        variety=rate["variety"],
        price_per_kg=rate["price_per_kg"],
        price_per_qtl=(rate["price_per_kg"] * KG_PER_QUINTAL).quantize(Decimal("0.01")),
        change_percentage=rate["change_percentage"],
        effective_date=rate["effective_date"],
    )


def paginated(rows: list, total: int, params) -> PaginatedResponse:
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=rows,
        pagination=Pagination(
            page=params.page, page_size=params.page_size, total=total, total_pages=total_pages
        ),
    )
