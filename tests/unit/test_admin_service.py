from decimal import Decimal

import pytest

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import UserRole, UserStatus
from app.services import admin_service

pytestmark = pytest.mark.asyncio


async def test_create_manager_and_duplicate_phone_rejected(db_session, super_admin):
    manager = await admin_service.create_manager(
        db_session, creator=super_admin, name="New Manager", phone="9555566677", email=None,
        password="managerpass123", role=UserRole.MANAGER, assigned_region="North", department="Field Ops",
    )
    await db_session.commit()
    assert manager.status == UserStatus.ACTIVE  # staff accounts skip the approval gate

    with pytest.raises(ConflictError):
        await admin_service.create_manager(
            db_session, creator=super_admin, name="Duplicate", phone="9555566677", email=None,
            password="anotherpass123", role=UserRole.MANAGER, assigned_region=None, department=None,
        )


async def test_update_manager_status(db_session, super_admin):
    manager = await admin_service.create_manager(
        db_session, creator=super_admin, name="Status Manager", phone="9666677788", email=None,
        password="managerpass123", role=UserRole.MANAGER, assigned_region=None, department=None,
    )
    await db_session.commit()

    updated = await admin_service.update_manager_status(
        db_session, actor=super_admin, manager_id=manager.id, new_status=UserStatus.SUSPENDED
    )
    assert updated.status == UserStatus.SUSPENDED


async def test_reset_manager_password_revokes_sessions(db_session, super_admin):
    from app.core.security import verify_password
    from app.services import auth_service

    manager = await admin_service.create_manager(
        db_session, creator=super_admin, name="Reset Manager", phone="9777788899", email=None,
        password="oldpassword123", role=UserRole.MANAGER, assigned_region=None, department=None,
    )
    await db_session.commit()

    _, _, _ = await auth_service.issue_token_pair(db_session, user=manager)
    await db_session.commit()

    await admin_service.reset_manager_password(
        db_session, actor=super_admin, manager_id=manager.id, new_password="newpassword456"
    )
    await db_session.commit()

    assert verify_password("newpassword456", manager.password_hash)
    assert not verify_password("oldpassword123", manager.password_hash)


async def test_reset_password_on_missing_manager_raises(db_session, super_admin):
    import uuid

    with pytest.raises(NotFoundError):
        await admin_service.reset_manager_password(
            db_session, actor=super_admin, manager_id=uuid.uuid4(), new_password="whatever123"
        )


async def test_dashboard_reflects_seeded_data(db_session, active_farmer, super_admin):
    from app.services import purchase_service

    await purchase_service.create_seed(
        db_session, admin=super_admin, name="Dashboard Seed", variety=None,
        price_per_kg=Decimal("10"), stock_kg=Decimal("123"), description=None, image_url=None,
    )
    await db_session.commit()

    dashboard = await admin_service.get_dashboard(db_session)
    assert dashboard["total_farmers"] >= 1
    assert dashboard["total_seed_stock_kg"] >= Decimal("123")


async def test_monthly_report_returns_zero_for_empty_month(db_session):
    report = await admin_service.get_monthly_report(db_session, month="2019-01")
    assert report["total_seed_purchases_amount"] == Decimal("0")
    assert report["total_bookings"] == 0
