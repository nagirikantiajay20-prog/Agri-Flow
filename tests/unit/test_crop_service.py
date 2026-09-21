from datetime import date
from decimal import Decimal

import pytest

from app.models.crop import DEFAULT_VISIT_MONTHS, VISIT_MONTHS_BY_CROP
from app.services import crop_service

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("crop_type", ["Rice", "Maize", "Cotton", "Sugarcane", "Turmeric"])
async def test_crop_registration_schedules_correct_visit_count(db_session, active_farmer, crop_type):
    crop = await crop_service.register_crop(
        db_session, farmer=active_farmer, crop_type=crop_type, acres=Decimal("2"), sowing_date=date(2026, 6, 1)
    )
    await db_session.commit()

    visits, _total = await crop_service.list_visits_for_actor(db_session, actor=active_farmer)
    my_visits = [v for v in visits if v.crop_id == crop.id]
    expected_months = VISIT_MONTHS_BY_CROP.get(crop_type, DEFAULT_VISIT_MONTHS)
    assert len(my_visits) == len(expected_months)
    assert sorted(v.visit_month for v in my_visits) == sorted(expected_months)


async def test_unknown_crop_type_falls_back_to_default_schedule(db_session, active_farmer):
    crop = await crop_service.register_crop(
        db_session, farmer=active_farmer, crop_type="ExoticFruit", acres=Decimal("1"), sowing_date=date(2026, 1, 1)
    )
    await db_session.commit()
    visits, _total = await crop_service.list_visits_for_actor(db_session, actor=active_farmer)
    my_visits = [v for v in visits if v.crop_id == crop.id]
    assert sorted(v.visit_month for v in my_visits) == sorted(DEFAULT_VISIT_MONTHS)


async def test_farmer_only_sees_own_crops(db_session, active_farmer, super_admin):
    from app.core.security import hash_password
    from app.models.enums import UserRole, UserStatus
    from app.models.user import User

    other = User(
        name="Other Crop Farmer", phone="9444455566", password_hash=hash_password("x"),
        role=UserRole.FARMER, status=UserStatus.ACTIVE,
    )
    db_session.add(other)
    await db_session.flush()

    await crop_service.register_crop(
        db_session, farmer=active_farmer, crop_type="Rice", acres=Decimal("1"), sowing_date=date(2026, 1, 1)
    )
    await crop_service.register_crop(
        db_session, farmer=other, crop_type="Wheat", acres=Decimal("1"), sowing_date=date(2026, 1, 1)
    )
    await db_session.commit()

    my_crops, _ = await crop_service.list_crops_for_actor(db_session, actor=active_farmer)
    assert all(c.farmer_id == active_farmer.id for c in my_crops)

    admin_crops, _ = await crop_service.list_crops_for_actor(db_session, actor=super_admin)
    assert len(admin_crops) >= 2  # admin sees everyone's
