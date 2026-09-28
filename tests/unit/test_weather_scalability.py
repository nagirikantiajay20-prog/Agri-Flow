"""Unit tests for weather service concurrency deduplication and circuit breaker."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services import weather_service


@pytest.fixture(autouse=True)
def reset_weather():
    weather_service.reset_weather_state()
    yield
    weather_service.reset_weather_state()


@pytest.mark.asyncio
async def test_weather_single_flight_deduplication(monkeypatch):
    """Verify that multiple concurrent callers for the same coordinate trigger only 1 upstream call."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)

    upstream_call_count = 0

    async def mock_get(url, params=None):
        nonlocal upstream_call_count
        upstream_call_count += 1
        # Add slight delay so concurrent tasks are in-flight together
        await asyncio.sleep(0.05)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "current": {
                "temperature_2m": 29.0,
                "apparent_temperature": 31.0,
                "relative_humidity_2m": 65.0,
                "wind_speed_10m": 12.0,
                "weather_code": 1,
                "precipitation": 0.0,
            }
        }
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    with patch("app.services.weather_service.is_available", return_value=False), \
         patch("httpx.AsyncClient.get", side_effect=mock_get):
        
        # Fire 5 concurrent requests for the exact same coordinate
        results = await asyncio.gather(*[
            weather_service.current_weather(16.5, 80.5)
            for _ in range(5)
        ])

        # All 5 should have received identical valid weather
        assert len(results) == 5
        for res in results:
            assert res is not None
            assert res["temperature_c"] == 29
            assert res["condition"] == "Mainly clear"

        # Upstream should have been called EXACTLY ONCE due to single-flight deduplication
        assert upstream_call_count == 1


@pytest.mark.asyncio
async def test_weather_circuit_breaker_prevents_repeated_upstream_failures(monkeypatch):
    """Verify circuit breaker trips on upstream error and avoids slamming upstream repeatedly."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)

    upstream_attempts = 0

    async def failing_get(url, params=None):
        nonlocal upstream_attempts
        upstream_attempts += 1
        raise Exception("Open-Meteo 503 Service Unavailable")

    with patch("app.services.weather_service.is_available", return_value=False), \
         patch("httpx.AsyncClient.get", side_effect=failing_get):

        # First call fails and trips the breaker
        res1 = await weather_service.current_weather(17.0, 81.0)
        assert res1 is None
        assert upstream_attempts == 1

        # Second call immediately after should be blocked by circuit breaker (0 upstream attempts)
        res2 = await weather_service.current_weather(17.0, 81.0)
        assert res2 is None
        assert upstream_attempts == 1  # Still 1! Circuit breaker blocked the second call
