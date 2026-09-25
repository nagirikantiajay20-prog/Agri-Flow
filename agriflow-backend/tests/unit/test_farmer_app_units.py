import uuid

import pytest

from app.schemas.farmer_app import mask_account, normalize_phone
from app.services import push_service, weather_service

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "raw", ["9502662924", "+91 95026 62924", "+919502662924", "09502662924", "95026-62924"]
)
async def test_phone_numbers_normalise_to_ten_digits(raw):
    assert normalize_phone(raw) == "9502662924"


@pytest.mark.parametrize("raw", ["12345", "+1 202 555 0100", "abcdefghij", ""])
async def test_invalid_phone_numbers_are_rejected(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


async def test_bank_account_is_masked_to_last_four():
    assert mask_account("10293847561") == "*******7561"
    assert mask_account(None) is None


async def test_push_is_sent_only_after_commit(monkeypatch):
    from app.core import database

    sent: list[list] = []
    monkeypatch.setattr(push_service, "dispatch_after_commit", lambda pushes: sent.append(pushes))

    gen = database.get_db()
    session = await gen.__anext__()
    push_service.enqueue(session, push_service.Push(user_id=uuid.uuid4(), title="t", body="b", data={}))
    assert sent == [], "Nothing may be sent while the transaction is still open"
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert len(sent) == 1 and len(sent[0]) == 1


async def test_push_is_dropped_when_the_transaction_rolls_back(monkeypatch):
    from app.core import database

    sent: list[list] = []
    monkeypatch.setattr(push_service, "dispatch_after_commit", lambda pushes: sent.append(pushes))

    gen = database.get_db()
    session = await gen.__anext__()
    push_service.enqueue(session, push_service.Push(user_id=uuid.uuid4(), title="t", body="b", data={}))
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("stock check failed"))
    assert sent == [], "A rolled-back action must never produce a push"


async def test_push_is_disabled_without_credentials(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FIREBASE_CREDENTIALS_JSON", "")
    monkeypatch.setattr(settings, "FIREBASE_CREDENTIALS_PATH", "")
    push_service._firebase_app.cache_clear()
    assert push_service.is_enabled() is False
    push_service.dispatch_after_commit([push_service.Push(user_id=uuid.uuid4(), title="t", body="b", data={})])


@pytest.mark.parametrize(
    ("temp", "humidity", "wind", "rain", "code", "expected"),
    [
        (31, 58, 9, 0, 0, "Good day to irrigate before noon"),
        (28, 60, 30, 0, 1, "High wind — avoid pesticide spraying today"),
        (26, 90, 5, 5, 63, "Rain expected — postpone spraying and irrigation"),
        (30, 60, 5, 0, 95, "Thunderstorm risk — avoid field work and secure harvested produce"),
        (40, 30, 5, 0, 0, "Heat stress risk — irrigate in the early morning or evening"),
    ],
)
async def test_weather_advisory_rules(temp, humidity, wind, rain, code, expected):
    assert weather_service.advisory_for(temp, humidity, wind, rain, code) == expected


async def test_weather_parses_open_meteo_payload(fake_redis, monkeypatch):
    import httpx

    payload = {"current": {"temperature_2m": 31.4, "apparent_temperature": 34.2, "relative_humidity_2m": 58,
                           "wind_speed_10m": 9.1, "weather_code": 0, "precipitation": 0}}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    weather = await weather_service.current_weather()
    assert weather == {"temperature_c": 31, "feels_like_c": 34, "condition": "Clear",
                       "advisory": "Good day to irrigate before noon", "humidity_percent": 58, "wind_kmh": 9}
    assert await fake_redis.get("cache:weather:15.83:78.04") is not None


async def test_weather_failure_returns_none(fake_redis, monkeypatch):
    import httpx

    class _Broken:
        def __init__(self, *a, **k):
            raise httpx.ConnectError("offline")

    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(httpx, "AsyncClient", _Broken)
    assert await weather_service.current_weather(latitude=1.0, longitude=1.0) is None


async def test_rate_limit_keys_authenticated_traffic_on_the_user(active_farmer):
    from starlette.requests import Request

    from app.middleware.rate_limit import _principal
    from tests.utils import auth_headers

    def request(headers: dict) -> Request:
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    assert _principal(request(auth_headers(active_farmer)), "10.0.0.1") == f"user:{active_farmer.id}"
    assert _principal(request({"Authorization": "Bearer forged"}), "10.0.0.1") == "ip:10.0.0.1"
    assert _principal(request({}), "10.0.0.1") == "ip:10.0.0.1"


async def test_firebase_app_is_initialised_once_and_stays_enabled(monkeypatch):
    import firebase_admin
    from firebase_admin import credentials

    from app.core.config import settings

    calls = []
    monkeypatch.setattr(credentials, "Certificate", lambda source: ("cert", source))
    monkeypatch.setattr(firebase_admin, "initialize_app", lambda cred, name: calls.append(name) or object())
    monkeypatch.setattr(settings, "FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    push_service._firebase_app.cache_clear()
    try:
        assert all(push_service.is_enabled() for _ in range(3))
        assert calls == ["agriflow"], "initialize_app must run once; a second call raises 'app already exists'"
    finally:
        push_service._firebase_app.cache_clear()


async def test_google_application_credentials_is_honoured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FIREBASE_CREDENTIALS_JSON", "")
    monkeypatch.setattr(settings, "FIREBASE_CREDENTIALS_PATH", "")
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", "/etc/secrets/firebase-service-account.json")
    assert push_service._credential_source() == "/etc/secrets/firebase-service-account.json"
