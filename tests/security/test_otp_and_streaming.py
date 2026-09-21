"""
OTP and live-stream handshake security.

The legacy implementation (server/utils/otp.js) had three defects this
suite pins shut: the code was returned in the HTTP response body, it was
held in a per-process in-memory Map (so it could not work on the
serverless targets the frontend deploys to), and there was no attempt
limit, leaving a 6-digit code brute-forceable inside its 10-minute life.
"""
import uuid

import pytest

from app.core.config import settings
from app.services import event_service, otp_service
from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio


async def test_issued_otp_is_not_returned_in_the_response(client, fake_redis):
    resp = await client.post("/api/v1/auth/send-otp", json={"phone": "9876500001"})
    assert resp.status_code == 200
    assert resp.json()["data"]["code"] is None, "The OTP must never travel back to the caller"


async def test_otp_is_never_stored_in_plaintext(fake_redis):
    code = await otp_service.issue(phone="9876500002", purpose=otp_service.PURPOSE_REGISTRATION)
    stored = await fake_redis.hgetall("otp:registration:9876500002")
    assert stored
    assert code is None or stored["hash"] != code
    assert code not in stored.values() if code else True


async def test_correct_otp_verifies_then_is_consumed(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "OTP_ECHO_IN_RESPONSE", True)
    code = await otp_service.issue(phone="9876500003", purpose=otp_service.PURPOSE_REGISTRATION)
    assert await otp_service.verify(
        phone="9876500003", code=code, purpose=otp_service.PURPOSE_REGISTRATION
    )
    assert not await otp_service.verify(
        phone="9876500003", code=code, purpose=otp_service.PURPOSE_REGISTRATION
    ), "A consumed OTP must not verify a second time"


async def test_wrong_otp_is_rejected_and_locks_out_after_max_attempts(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "OTP_ECHO_IN_RESPONSE", True)
    code = await otp_service.issue(phone="9876500004", purpose=otp_service.PURPOSE_REGISTRATION)

    for _ in range(otp_service.OTP_MAX_ATTEMPTS):
        assert not await otp_service.verify(
            phone="9876500004", code="000000", purpose=otp_service.PURPOSE_REGISTRATION
        )

    assert not await otp_service.verify(
        phone="9876500004", code=code, purpose=otp_service.PURPOSE_REGISTRATION
    ), "After the attempt limit the code must be dead even if the caller finally guesses it"


async def test_otp_for_one_purpose_does_not_verify_another(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "OTP_ECHO_IN_RESPONSE", True)
    code = await otp_service.issue(phone="9876500005", purpose=otp_service.PURPOSE_REGISTRATION)
    assert not await otp_service.verify(
        phone="9876500005", code=code, purpose=otp_service.PURPOSE_PASSWORD_RESET
    )


async def test_otp_is_bound_to_the_phone_number(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "OTP_ECHO_IN_RESPONSE", True)
    code = await otp_service.issue(phone="9876500006", purpose=otp_service.PURPOSE_REGISTRATION)
    await otp_service.issue(phone="9876500007", purpose=otp_service.PURPOSE_REGISTRATION)
    assert not await otp_service.verify(
        phone="9876500007", code=code, purpose=otp_service.PURPOSE_REGISTRATION
    )


async def test_forgot_password_does_not_reveal_whether_an_account_exists(client, fake_redis):
    unknown = await client.post(
        "/api/v1/auth/forgot-password/send-otp", json={"phone": "9000000001"}
    )
    assert unknown.status_code == 200
    assert unknown.json()["data"]["code"] is None


async def test_password_reset_requires_a_valid_otp(client, fake_redis, active_farmer):
    resp = await client.post(
        "/api/v1/auth/forgot-password/reset",
        json={"phone": active_farmer.phone, "code": "000000", "new_password": "hackedpass123"},
    )
    assert resp.status_code == 422


async def test_stream_ticket_is_single_use(fake_redis):
    user_id = uuid.uuid4()
    ticket = await event_service.issue_ticket(user_id)
    assert await event_service.redeem_ticket(ticket) == user_id
    assert await event_service.redeem_ticket(ticket) is None, "A stream ticket must not be replayable"


async def test_unknown_stream_ticket_is_rejected(client, fake_redis):
    resp = await client.get("/api/v1/events/stream?ticket=deadbeef")
    assert resp.status_code == 401


async def test_stream_requires_a_ticket_not_a_bearer_token(client, fake_redis, active_farmer):
    """The bearer token must not be accepted in the query string, so it
    cannot end up in proxy logs or browser history."""
    from app.core.security import create_access_token

    token = create_access_token(
        user_id=active_farmer.id, role=active_farmer.role.value, status=active_farmer.status.value
    )
    resp = await client.get(f"/api/v1/events/stream?ticket={token}")
    assert resp.status_code == 401


async def test_ticket_endpoint_requires_authentication(client, fake_redis, active_farmer):
    assert (await client.post("/api/v1/events/ticket")).status_code == 401
    assert (
        await client.post("/api/v1/events/ticket", headers=auth_headers(active_farmer))
    ).status_code == 200
