"""
IDOR regression test — Master Plan §1.5: the legacy farmer-api took
farmer_id from the request payload for most reads. Every "own resource"
endpoint here must derive identity from the JWT and must not expose
another user's data even if a client tries to reference it by ID.
"""
import pytest

from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio


async def test_farmer_cannot_read_another_farmers_notification(client, db_session, active_farmer):
    from app.core.security import hash_password
    from app.models.enums import UserRole, UserStatus
    from app.models.user import User
    from app.services import notification_service

    other = User(
        name="Other Farmer", phone="9111122233", password_hash=hash_password("x"),
        role=UserRole.FARMER, status=UserStatus.ACTIVE,
    )
    db_session.add(other)
    await db_session.flush()
    note = await notification_service.notify_user(
        db_session, user_id=other.id, title="Private", message="Not yours"
    )
    await db_session.commit()

    # active_farmer (a DIFFERENT user) tries to mark someone else's
    # notification as read — must be rejected, not silently succeed.
    resp = await client.patch(
        f"/api/v1/notifications/{note.id}/read", headers=auth_headers(active_farmer)
    )
    assert resp.status_code == 404, "Must not be able to act on another user's notification"


async def test_registration_cannot_self_assign_privileged_role(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Sneaky", "phone": "9222233344", "password": "testpass123", "role": "super_admin"},
    )
    assert resp.status_code == 422, "Public registration must reject any role other than farmer"


async def test_farmer_id_is_never_taken_from_request_body(client, active_farmer):
    """POST /crops does not accept a farmer_id field at all — even if a
    client sends one, it's ignored because CropCreate has no such field
    and the service always uses the authenticated actor's id."""
    resp = await client.post(
        "/api/v1/crops",
        json={"crop_type": "Rice", "acres": "2.5", "sowing_date": "2026-06-01", "farmer_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth_headers(active_farmer),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["data"]["farmer_id"] == str(active_farmer.id), "Crop must be attributed to the authenticated user, not a client-supplied id"
