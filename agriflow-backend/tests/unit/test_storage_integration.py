"""
Storage/upload tests — Master Plan Module 12. Mocks the outbound HTTP
call to Supabase Storage so this suite doesn't need real storage
credentials, while still exercising every validation branch in
app.integrations.storage (bucket allow-list, content-type allow-list,
size limit, extension check) — the things this module is actually
responsible for getting right, independent of which storage provider
is configured.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import ValidationError
from app.integrations import storage

pytestmark = pytest.mark.asyncio


async def test_rejects_unknown_bucket():
    with pytest.raises(ValidationError, match="Unknown bucket"):
        await storage.create_presigned_upload(
            bucket_key="not-a-real-bucket", file_name="a.jpg", content_type="image/jpeg", size_bytes=1000
        )


async def test_rejects_disallowed_content_type():
    with pytest.raises(ValidationError, match="not allowed"):
        await storage.create_presigned_upload(
            bucket_key="documents", file_name="a.exe", content_type="application/x-msdownload", size_bytes=1000
        )


async def test_rejects_oversized_file():
    from app.core.config import settings

    too_big = settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024 + 1
    with pytest.raises(ValidationError, match="exceeds"):
        await storage.create_presigned_upload(
            bucket_key="documents", file_name="a.pdf", content_type="application/pdf", size_bytes=too_big
        )


async def test_rejects_file_with_no_extension():
    with pytest.raises(ValidationError, match="extension"):
        await storage.create_presigned_upload(
            bucket_key="documents", file_name="noextension", content_type="application/pdf", size_bytes=1000
        )


async def test_rejects_disallowed_extension_even_with_allowed_content_type():
    with pytest.raises(ValidationError, match="not allowed"):
        await storage.create_presigned_upload(
            bucket_key="seeds", file_name="script.exe", content_type="image/png", size_bytes=1000
        )


async def test_missing_supabase_config_raises_clear_error():
    with pytest.raises(ValidationError, match="not configured"):
        await storage.create_presigned_upload(
            bucket_key="documents", file_name="a.pdf", content_type="application/pdf", size_bytes=1000
        )


async def test_successful_presign_calls_supabase_and_returns_url():
    from app.core.config import settings

    original_url = settings.SUPABASE_URL
    original_key = settings.SUPABASE_SERVICE_ROLE_KEY
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_SERVICE_ROLE_KEY = "fake-key"
    try:
        mock_response = AsyncMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json = lambda: {"url": "/object/upload/sign/documents/fake-path?token=abc"}

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            result = await storage.create_presigned_upload(
                bucket_key="documents", file_name="passbook.pdf", content_type="application/pdf", size_bytes=2048
            )
        assert result["method"] == "PUT"
        assert result["object_path"].startswith("farmer-documents/")
        assert result["object_path"].endswith(".pdf")
        assert "example.supabase.co" in result["upload_url"]
    finally:
        settings.SUPABASE_URL = original_url
        settings.SUPABASE_SERVICE_ROLE_KEY = original_key
