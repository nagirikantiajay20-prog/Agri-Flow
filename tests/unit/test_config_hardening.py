"""
Config hardening tests — gap-fix #8. Uses a standalone Settings()
instance rather than the module-level `settings` singleton, so these
tests don't depend on / mutate the real .env-loaded config.
"""
import pytest

from app.core.config import Settings


def _base_kwargs(**overrides) -> dict:
    base = dict(
        DATABASE_URL="postgresql+asyncpg://user:pass@db.supabase.co:5432/postgres",
        JWT_SECRET_KEY="a" * 40,
        CORS_ALLOWED_ORIGINS="https://agriflow.example.com",
        REDIS_URL="redis://prod-redis.internal:6379/0",
        STORAGE_PROVIDER="supabase",
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="key123",
    )
    base.update(overrides)
    return base


def test_fully_safe_config_has_no_problems():
    s = Settings(**_base_kwargs())
    assert s.validate_for_production() == []


def test_placeholder_jwt_secret_flagged():
    s = Settings(**_base_kwargs(JWT_SECRET_KEY="change-me-to-a-long-random-string"))
    problems = s.validate_for_production()
    assert any("placeholder" in p for p in problems)


def test_short_jwt_secret_flagged():
    s = Settings(**_base_kwargs(JWT_SECRET_KEY="short"))
    problems = s.validate_for_production()
    assert any("32" in p for p in problems)


def test_localhost_database_url_flagged():
    s = Settings(**_base_kwargs(DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/agriflow"))
    problems = s.validate_for_production()
    assert any("DATABASE_URL" in p for p in problems)


def test_wildcard_cors_flagged():
    s = Settings(**_base_kwargs(CORS_ALLOWED_ORIGINS="*"))
    problems = s.validate_for_production()
    assert any("wildcard" in p for p in problems)


def test_localhost_redis_flagged():
    s = Settings(**_base_kwargs(REDIS_URL="redis://localhost:6379/0"))
    problems = s.validate_for_production()
    assert any("REDIS_URL" in p for p in problems)


def test_missing_supabase_credentials_flagged():
    s = Settings(**_base_kwargs(SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY=""))
    problems = s.validate_for_production()
    assert any("SUPABASE" in p for p in problems)


def test_overly_long_access_token_expiry_flagged():
    s = Settings(**_base_kwargs(ACCESS_TOKEN_EXPIRE_MINUTES=120))
    problems = s.validate_for_production()
    assert any("ACCESS_TOKEN_EXPIRE_MINUTES" in p for p in problems)


@pytest.mark.asyncio
async def test_app_startup_raises_in_production_with_unsafe_config(monkeypatch):
    import app.main as m

    monkeypatch.setattr(m.settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="Refusing to start in production"):
        async with m.lifespan(m.app):
            pass
    monkeypatch.setattr(m.settings, "ENVIRONMENT", "development")


@pytest.mark.asyncio
async def test_app_startup_only_warns_in_development():
    import app.main as m

    # Should not raise — dev config is allowed to be "unsafe" by design.
    async with m.lifespan(m.app):
        pass
