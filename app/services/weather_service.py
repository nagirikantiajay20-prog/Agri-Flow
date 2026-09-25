"""
Weather advisory for the farmer home screen.

Backed by Open-Meteo (https://open-meteo.com), which needs no API key.
Results are cached per ~1 km grid cell in Redis for WEATHER_CACHE_SECONDS,
so a thousand farmers opening the app in one village cost one upstream
call. Any upstream failure returns None: the dashboard renders without a
weather card rather than failing.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis, is_available, mark_unavailable

logger = get_logger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

_WMO_CONDITIONS: dict[int, str] = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def advisory_for(temperature_c: float, humidity: float, wind_kmh: float, precipitation_mm: float, code: int) -> str:
    if code >= 95:
        return "Thunderstorm risk — avoid field work and secure harvested produce"
    if precipitation_mm > 2 or code in (63, 65, 81, 82):
        return "Rain expected — postpone spraying and irrigation"
    if wind_kmh > 25:
        return "High wind — avoid pesticide spraying today"
    if temperature_c >= 38:
        return "Heat stress risk — irrigate in the early morning or evening"
    if humidity >= 85:
        return "High humidity — watch for fungal disease, inspect leaves"
    if temperature_c >= 30:
        return "Good day to irrigate before noon"
    return "Favourable conditions for field work"


def _parse(payload: dict) -> dict:
    current = payload["current"]
    temperature = float(current["temperature_2m"])
    feels_like = float(current["apparent_temperature"])
    humidity = float(current["relative_humidity_2m"])
    wind = float(current["wind_speed_10m"])
    precipitation = float(current.get("precipitation") or 0)
    code = int(current["weather_code"])
    return {
        "temperature_c": round(temperature),
        "feels_like_c": round(feels_like),
        "condition": _WMO_CONDITIONS.get(code, "Unknown"),
        "advisory": advisory_for(temperature, humidity, wind, precipitation, code),
        "humidity_percent": round(humidity),
        "wind_kmh": round(wind),
    }


async def current_weather(latitude: float | None = None, longitude: float | None = None) -> dict | None:
    if not settings.WEATHER_ENABLED:
        logger.info("weather_disabled_by_config")
        return None
    lat = round(latitude if latitude is not None else settings.WEATHER_DEFAULT_LATITUDE, 2)
    lon = round(longitude if longitude is not None else settings.WEATHER_DEFAULT_LONGITUDE, 2)
    cache_key = f"cache:weather:{lat}:{lon}"
    stale_key = f"cache:weather:stale:{lat}:{lon}"

    if is_available():
        try:
            hit = await asyncio.wait_for(get_redis().get(cache_key), timeout=0.5)
            if hit:
                return json.loads(hit)
        except Exception as exc:
            logger.warning("weather_redis_get_failed", error=str(exc))
            mark_unavailable()

    try:
        async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": "AgriFlow-Backend/1.0"}) as client:
            resp = await client.get(
                OPEN_METEO_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "wind_speed_10m,weather_code,precipitation",
                    "wind_speed_unit": "kmh",
                    "timezone": "auto",
                },
            )
            resp.raise_for_status()
            weather = _parse(resp.json())
    except Exception as exc:
        logger.warning("weather_fetch_failed", error=str(exc))
        if is_available():
            try:
                stale_hit = await asyncio.wait_for(get_redis().get(stale_key), timeout=0.5)
                if stale_hit:
                    stale_data = json.loads(stale_hit)
                    stale_data["is_stale"] = True
                    return stale_data
            except Exception as s_exc:
                logger.warning("weather_redis_stale_get_failed", error=str(s_exc))
        return None

    if is_available():
        try:
            r = get_redis()
            await asyncio.wait_for(r.set(cache_key, json.dumps(weather), ex=settings.WEATHER_CACHE_SECONDS), timeout=0.5)
            await asyncio.wait_for(r.set(stale_key, json.dumps(weather), ex=86400), timeout=0.5)  # 24 hour stale fallback
        except Exception as exc:
            logger.warning("weather_redis_set_failed", error=str(exc))
            mark_unavailable()
    return weather
