from decimal import Decimal

import pytest

from app.core.exceptions import ConflictError
from app.models.enums import UserStatus
from app.services import farmer_service

pytestmark = pytest.mark.asyncio


async def test_get_and_update_own_profile(db_session, active_farmer):
    user, profile = await farmer_service.get_own_profile(db_session, user=active_farmer)
    assert user.id == active_farmer.id
    assert profile.acres_of_land == Decimal("0")

    updated = await farmer_service.update_own_profile(
        db_session, user=active_farmer, updates={"acres_of_land": Decimal("5.5"), "soil_type": "loamy"}
    )
    assert updated.acres_of_land == Decimal("5.5")
    assert updated.soil_type == "loamy"


async def test_bank_change_request_blocks_second_pending_request(db_session, active_farmer):
    await farmer_service.request_bank_change(
        db_session, farmer=active_farmer, bank_name="Bank A", account_number="12345",
        ifsc_code="IFSC0001", upi_id=None,
    )
    await db_session.commit()

    with pytest.raises(ConflictError):
        await farmer_service.request_bank_change(
            db_session, farmer=active_farmer, bank_name="Bank B", account_number="67890",
            ifsc_code="IFSC0002", upi_id=None,
        )


async def test_bank_change_review_approves_and_updates_profile(db_session, active_farmer, super_admin):
    req = await farmer_service.request_bank_change(
        db_session, farmer=active_farmer, bank_name="Bank A", account_number="99999",
        ifsc_code="IFSC0099", upi_id="farmer@upi",
    )
    await db_session.commit()

    reviewed = await farmer_service.review_bank_change(
        db_session, reviewer=super_admin, request_id=req.id, approve=True, admin_notes="looks good"
    )
    assert reviewed.status.value == "approved"

    _, profile = await farmer_service.get_own_profile(db_session, user=active_farmer)
    assert profile.account_number == "99999"
    assert profile.bank_name == "Bank A"


async def test_bank_change_review_rejects_without_updating_profile(db_session, active_farmer, super_admin):
    req = await farmer_service.request_bank_change(
        db_session, farmer=active_farmer, bank_name="Bank C", account_number="11111",
        ifsc_code="IFSC0011", upi_id=None,
    )
    await db_session.commit()

    reviewed = await farmer_service.review_bank_change(
        db_session, reviewer=super_admin, request_id=req.id, approve=False, admin_notes="insufficient docs"
    )
    assert reviewed.status.value == "rejected"

    _, profile = await farmer_service.get_own_profile(db_session, user=active_farmer)
    assert profile.account_number != "11111"


async def test_set_approval_transitions_status(db_session, active_farmer, super_admin):
    farmer = await farmer_service.set_approval(
        db_session, reviewer=super_admin, farmer_id=active_farmer.id, new_status=UserStatus.SUSPENDED
    )
    assert farmer.status == UserStatus.SUSPENDED
