import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core import redis
from app.core.database import session_scope
from app.models.user import User
from app.services import farmer_app_service, weather_service


async def test_1_open_meteo_success():
    print("\n--- Test 1: Open-Meteo Success (Live Fetch) ---")
    w = await weather_service.current_weather()
    assert w is not None, "Weather should not be None"
    assert "temperature_c" in w
    assert "feels_like_c" in w
    assert "condition" in w
    assert "advisory" in w
    assert "humidity_percent" in w
    assert "wind_kmh" in w
    print("PASS: Open-Meteo live fetch succeeded:", w)
    return w

async def test_2_redis_available_and_caching():
    print("\n--- Test 2: Redis Available & Caching ---")
    redis.mark_available()
    fake_store = {}

    class FakeRedis:
        async def get(self, key):
            val = fake_store.get(key)
            print(f"  [FakeRedis] GET {key} -> {val is not None}")
            return val

        async def set(self, key, val, ex=None):
            print(f"  [FakeRedis] SET {key} (ex={ex})")
            fake_store[key] = val

    with patch.object(weather_service, "is_available", return_value=True), \
         patch.object(weather_service, "get_redis", return_value=FakeRedis()):
        # First call: populates cache
        w1 = await weather_service.current_weather()
        assert w1 is not None
        assert len(fake_store) > 0, "Cache should have entry"

        # Second call: reads from cache
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.side_effect = RuntimeError("HTTP should not be called on cache hit!")
            w2 = await weather_service.current_weather()
            assert w2 == w1
            print("PASS: Cache hit returned identical weather without HTTP call.")

async def test_3_redis_unavailable_resilience():
    print("\n--- Test 3: Redis Unavailable / Broken Connection ---")
    class BrokenRedis:
        async def get(self, key):
            print("  [BrokenRedis] GET simulating ConnectionError")
            raise ConnectionError("Simulated Redis socket failure")

        async def set(self, key, val, ex=None):
            raise ConnectionError("Simulated Redis socket failure on SET")

    with patch.object(weather_service, "is_available", side_effect=[True, False]), \
         patch.object(weather_service, "get_redis", return_value=BrokenRedis()):
        w = await weather_service.current_weather()
        assert w is not None, "Weather must still be returned via Open-Meteo even if Redis GET fails!"
        assert "temperature_c" in w
        print("PASS: Handled Redis failure gracefully and fetched from Open-Meteo:", w)

async def test_4_open_meteo_failure():
    print("\n--- Test 4: Open-Meteo Failure Handling ---")
    with patch.object(weather_service, "is_available", return_value=False):
        with patch("httpx.AsyncClient") as mock_http:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.side_effect = TimeoutError("Simulated upstream timeout")
            mock_http.return_value = mock_client

            w = await weather_service.current_weather()
            assert w is None, "Weather should be None if upstream fails"
            print("PASS: Upstream failure safely returned None without crashing.")

async def test_5_complete_farmer_dashboard_with_weather():
    print("\n--- Test 5: Complete Farmer Dashboard with Live Weather ---")
    async with session_scope() as db:
        user = (await db.execute(select(User).where(User.phone == '9876543210'))).scalar_one()
        dash = await farmer_app_service.dashboard(db, farmer=user)
        assert dash is not None
        assert dash.get("farmer_id") is not None
        assert dash.get("weather") is not None
        print("PASS: Dashboard successfully loaded with weather:", dash["weather"])

async def test_6_dashboard_resilience_when_weather_fails():
    print("\n--- Test 6: Dashboard Resilience when Weather Fails ---")
    with patch.object(weather_service, "current_weather", side_effect=TimeoutError("Weather service timed out")):
        async with session_scope() as db:
            user = (await db.execute(select(User).where(User.phone == '9876543210'))).scalar_one()
            dash = await farmer_app_service.dashboard(db, farmer=user)
            assert dash is not None
            assert dash.get("weather") is None, "Weather should be None"
            assert dash.get("active_crops_count") is not None, "Other dashboard fields must remain intact"
            print("PASS: Dashboard gracefully degraded to weather=None without crashing other sections.")

async def main():
    await test_1_open_meteo_success()
    await test_2_redis_available_and_caching()
    await test_3_redis_unavailable_resilience()
    await test_4_open_meteo_failure()
    await test_5_complete_farmer_dashboard_with_weather()
    await test_6_dashboard_resilience_when_weather_fails()
    print("\n================ ALL 6 INTEGRATION TESTS PASSED ================\n")

if __name__ == "__main__":
    asyncio.run(main())
