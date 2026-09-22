"""
Centralized application settings.

All values are read from environment variables / .env (see .env.example).
Nothing here should be hardcoded per-environment — see Master Plan §47
(environment separation: development / staging / production must never
share credentials).

`validate_for_production()` is the actual enforcement point (gap-fix #8
in the backend remediation plan) — called from app.main's startup
lifespan when ENVIRONMENT=="production", so a misconfigured production
deploy fails loudly at boot instead of silently running with a dev-grade
JWT secret, wildcard CORS, or a Redis-less rate limiter.
"""
import re
from functools import lru_cache
from typing import Any, List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRETS = {"", "change-me-to-a-long-random-string", "dev-only-secret", "dev-only-secret-not-for-production"}
_LOCAL_HOST_PATTERN = re.compile(r"(localhost|127\.0\.0\.1|0\.0\.0\.0)", re.IGNORECASE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── App ──────────────────────────────────────────────────────────
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "AgriFlow Backend"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    # ── Database ─────────────────────────────────────────────────────
    DATABASE_URL: str
    DATABASE_URL_SYNC: str | None = None  # used by Alembic
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_USE_PGBOUNCER: bool | None = None

    @property
    def uses_pgbouncer(self) -> bool:
        if self.DB_USE_PGBOUNCER is not None:
            return self.DB_USE_PGBOUNCER
        return ":6543/" in self.DATABASE_URL or "pooler.supabase.com:6543" in self.DATABASE_URL

    # ── Auth ─────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── Redis ────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Storage ──────────────────────────────────────────────────────
    STORAGE_PROVIDER: str = "supabase"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    STORAGE_BUCKET_DOCUMENTS: str = "farmer-documents"
    STORAGE_BUCKET_VISITS: str = "farm-visits"
    STORAGE_BUCKET_SEEDS: str = "uploads"
    STORAGE_BUCKET_AVATARS: str = "avatars"
    STORAGE_BUCKET_CROP_SCANS: str = "crop-scans"
    UPLOAD_MAX_SIZE_MB: int = 10

    S3_BUCKET: str = ""
    S3_REGION: str = "ap-south-1"
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_PUBLIC_BASE_URL: str = ""
    S3_PRESIGN_EXPIRES_SECONDS: int = 900

    @field_validator(
        "STORAGE_PROVIDER", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
        "S3_BUCKET", "S3_REGION", "S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY", "S3_PUBLIC_BASE_URL", "FIREBASE_CREDENTIALS_JSON",
        mode="before"
    )
    @classmethod
    def _strip_string_quotes(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip('\'" \r\n\t')
        return v

    # ── Rate limiting ────────────────────────────────────────────────
    RATE_LIMIT_GLOBAL_PER_15MIN: int = 900
    RATE_LIMIT_AUTH_PER_15MIN: int = 20
    RATE_LIMIT_OTP_PER_MIN: int = 3
    RATE_LIMIT_WRITE_PER_5MIN: int = 30
    RATE_LIMIT_UPLOAD_PER_15MIN: int = 10
    RATE_LIMIT_ADMIN_PER_5MIN: int = 100

    # ── OTP ──────────────────────────────────────────────────────────
    OTP_ECHO_IN_RESPONSE: bool = False
    REQUIRE_REGISTRATION_OTP: bool = False

    # ── Caching / live updates ───────────────────────────────────────
    CACHE_ENABLED: bool = True
    CACHE_TTL_PUBLIC_SECONDS: int = 60
    CACHE_TTL_DASHBOARD_SECONDS: int = 10
    LIVE_UPDATES_ENABLED: bool = True

    # ── Push notifications (FCM) ─────────────────────────────────────
    FIREBASE_CREDENTIALS_JSON: str = ""
    FIREBASE_CREDENTIALS_PATH: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    # ── Weather advisory (Open-Meteo, keyless) ───────────────────────
    WEATHER_ENABLED: bool = True
    WEATHER_DEFAULT_LATITUDE: float = 15.8281
    WEATHER_DEFAULT_LONGITUDE: float = 78.0373
    WEATHER_CACHE_SECONDS: int = 900

    # ── Farmer mobile app ────────────────────────────────────────────
    RATE_LIMIT_LOGIN_PER_MIN: int = 5
    UPLOAD_MAX_AVATAR_MB: int = 2

    # ── Metrics (gap-fix #9) ─────────────────────────────────────────
    METRICS_ENABLED: bool = True

    def validate_for_production(self) -> list[str]:
        """Returns a list of human-readable problems. Empty list means
        the config is safe to run in production. Called unconditionally
        at startup (app.main.lifespan) but only enforced (raises) when
        ENVIRONMENT == "production" — in dev/test these same checks are
        useful as warnings but must never block local work."""
        problems: list[str] = []

        if self.JWT_SECRET_KEY in _PLACEHOLDER_SECRETS:
            problems.append("JWT_SECRET_KEY is a placeholder value — set a real random secret")
        elif len(self.JWT_SECRET_KEY) < 32:
            problems.append(f"JWT_SECRET_KEY is only {len(self.JWT_SECRET_KEY)} chars — use at least 32")

        if _LOCAL_HOST_PATTERN.search(self.DATABASE_URL):
            problems.append("DATABASE_URL points at localhost/127.0.0.1/0.0.0.0 — use the real production host")

        if "*" in self.CORS_ALLOWED_ORIGINS:
            problems.append("CORS_ALLOWED_ORIGINS contains a wildcard — list explicit production origins")
        if any(_LOCAL_HOST_PATTERN.search(o) for o in self.cors_origins_list):
            problems.append("CORS_ALLOWED_ORIGINS contains a localhost origin — remove it for production")

        if _LOCAL_HOST_PATTERN.search(self.REDIS_URL):
            problems.append(
                "REDIS_URL points at localhost — rate limiting will fail open against the wrong Redis "
                "in production if this is left pointing at a dev instance"
            )

        if self.STORAGE_PROVIDER == "supabase" and (not self.SUPABASE_URL or not self.SUPABASE_SERVICE_ROLE_KEY):
            problems.append("STORAGE_PROVIDER=supabase but SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not both set")
        if self.STORAGE_PROVIDER == "s3" and not (
            self.S3_BUCKET and self.S3_ACCESS_KEY_ID and self.S3_SECRET_ACCESS_KEY
        ):
            problems.append("STORAGE_PROVIDER=s3 but S3_BUCKET / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY are not all set")

        if self.OTP_ECHO_IN_RESPONSE:
            problems.append("OTP_ECHO_IN_RESPONSE is on — this returns the OTP in the API response and must never be enabled outside local development")

        if self.ACCESS_TOKEN_EXPIRE_MINUTES > 60:
            problems.append(
                f"ACCESS_TOKEN_EXPIRE_MINUTES={self.ACCESS_TOKEN_EXPIRE_MINUTES} is unusually long for an "
                "access token (Master Plan §15 recommends 15-30 min) — double-check this is intentional"
            )

        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

