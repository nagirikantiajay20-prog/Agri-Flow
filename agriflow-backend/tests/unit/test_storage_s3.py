"""
S3 storage provider.

Presigning is pure local SigV4 signing, so it runs against the real boto3
client with dummy credentials. The multipart upload replaces the client
with a stub so no network call leaves the test.
"""
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.integrations import storage

pytestmark = pytest.mark.asyncio


@pytest.fixture
def s3_settings(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_PROVIDER", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "agriflow-test")
    monkeypatch.setattr(settings, "S3_REGION", "ap-south-1")
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "")
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "AKIATESTTESTTEST")
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "secret/test/key")
    monkeypatch.setattr(settings, "S3_PUBLIC_BASE_URL", "")
    storage._s3_client.cache_clear()
    yield
    getattr(storage._s3_client, "cache_clear", lambda: None)()


async def test_presign_returns_a_signed_put_url_scoped_to_the_bucket_prefix(s3_settings):
    result = await storage.create_presigned_upload(
        bucket_key="documents", file_name="aadhaar.pdf", content_type="application/pdf", size_bytes=2048
    )
    url = urlparse(result["upload_url"])
    query = parse_qs(url.query)

    assert result["method"] == "PUT"
    assert result["object_path"].startswith(f"{settings.STORAGE_BUCKET_DOCUMENTS}/")
    assert result["object_path"].endswith(".pdf")
    assert "agriflow-test" in result["upload_url"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert "content-type" in query["X-Amz-SignedHeaders"][0]
    assert int(query["X-Amz-Expires"][0]) == settings.S3_PRESIGN_EXPIRES_SECONDS


async def test_presign_still_enforces_upload_validation(s3_settings):
    with pytest.raises(ValidationError):
        await storage.create_presigned_upload(
            bucket_key="documents", file_name="a.exe", content_type="application/x-msdownload", size_bytes=10
        )


async def test_presign_refuses_when_credentials_are_missing(s3_settings, monkeypatch):
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "")
    with pytest.raises(ValidationError, match="not configured"):
        await storage.create_presigned_upload(
            bucket_key="documents", file_name="a.pdf", content_type="application/pdf", size_bytes=10
        )


async def test_multipart_upload_puts_the_object_and_returns_a_signed_read_url(s3_settings, monkeypatch):
    calls = []

    class _Stub:
        def put_object(self, **kwargs):
            calls.append(kwargs)

        def generate_presigned_url(self, op, Params, ExpiresIn):
            return f"https://signed.example/{Params['Key']}?op={op}"

    monkeypatch.setattr(storage, "_s3_client", lambda: _Stub())

    result = await storage.upload_bytes(
        bucket_key="visits", file_name="field.jpg", content_type="image/jpeg", data=b"\xff\xd8jpeg"
    )

    assert len(calls) == 1
    assert calls[0]["Bucket"] == "agriflow-test"
    assert calls[0]["Key"] == result["object_path"]
    assert calls[0]["ContentType"] == "image/jpeg"
    assert result["public_url"].endswith("op=get_object")
    assert result["size_bytes"] == 6


async def test_public_base_url_is_used_instead_of_signing_when_configured(s3_settings, monkeypatch):
    monkeypatch.setattr(settings, "S3_PUBLIC_BASE_URL", "https://cdn.example.com/")

    class _Stub:
        def put_object(self, **kwargs):
            pass

    monkeypatch.setattr(storage, "_s3_client", lambda: _Stub())

    result = await storage.upload_bytes(
        bucket_key="seeds", file_name="seed.png", content_type="image/png", data=b"png"
    )
    assert result["public_url"] == f"https://cdn.example.com/{result['object_path']}"


async def test_production_config_flags_incomplete_s3_settings():
    from app.core.config import Settings

    s = Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@db.example.com:5432/x",
        JWT_SECRET_KEY="a" * 40,
        CORS_ALLOWED_ORIGINS="https://app.example.com",
        REDIS_URL="redis://cache.internal:6379/0",
        STORAGE_PROVIDER="s3",
        S3_BUCKET="",
    )
    assert any("STORAGE_PROVIDER=s3" in problem for problem in s.validate_for_production())


async def test_pgbouncer_is_detected_from_the_supabase_transaction_pooler_port():
    from app.core.config import Settings

    pooled = Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
        JWT_SECRET_KEY="a" * 40,
        DB_USE_PGBOUNCER=None,
    )
    direct = Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
        JWT_SECRET_KEY="a" * 40,
        DB_USE_PGBOUNCER=None,
    )
    assert pooled.uses_pgbouncer is True
    assert direct.uses_pgbouncer is False
