from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import NotFoundError
from app.models.enums import GrainGrade
from app.schemas.common import PageParams
from app.services import market_rate_service, notification_read_service, notification_service

pytestmark = pytest.mark.asyncio


async def test_mark_read_rejects_other_users_notification(db_session, active_farmer, super_admin):
    note = await notification_service.notify_user(
        db_session, user_id=super_admin.id, title="Admin only", message="hi"
    )
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await notification_read_service.mark_read(db_session, user_id=active_farmer.id, notification_id=note.id)


async def test_mark_all_read_only_affects_calling_user(db_session, active_farmer, super_admin):
    await notification_service.notify_user(db_session, user_id=active_farmer.id, title="A", message="a")
    await notification_service.notify_user(db_session, user_id=active_farmer.id, title="B", message="b")
    await notification_service.notify_user(db_session, user_id=super_admin.id, title="C", message="c")
    await db_session.commit()

    count = await notification_read_service.mark_all_read(db_session, user_id=active_farmer.id)
    assert count == 2

    admin_notes, _ = await notification_read_service.list_for_user(
        db_session, user_id=super_admin.id, params=PageParams(page=1, page_size=20)
    )
    assert all(not n.is_read for n in admin_notes if n.title == "C")


async def test_market_rate_latest_per_crop_grade(db_session, super_admin):
    # Unique crop_type avoids collision with rates committed by other
    # tests in the same session (tests commit real rows — see conftest;
    # `date.today()`-dated rates from other test files would otherwise
    # outrank this test's fixed 2026 dates for a shared crop_type).
    crop_type = "TestCropForMarketRate"
    await market_rate_service.set_rate(
        db_session, admin=super_admin, crop_type=crop_type, grade=GrainGrade.A,
        price_per_kg=Decimal("20.00"), effective_date=date(2026, 1, 1),
    )
    await market_rate_service.set_rate(
        db_session, admin=super_admin, crop_type=crop_type, grade=GrainGrade.A,
        price_per_kg=Decimal("25.00"), effective_date=date(2026, 6, 1),
    )
    await db_session.commit()

    rates = await market_rate_service.list_current_rates(db_session)
    rice_a = [r for r in rates if r.crop_type == crop_type and r.grade == GrainGrade.A]
    assert len(rice_a) == 1
    assert rice_a[0].price_per_kg == Decimal("25.00"), "Must return the latest effective rate, not the oldest"
