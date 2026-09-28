"""
Upload bucket authorization and private KYC storage security tests.
Verifies Issue #4 (Private KYC Storage) and Issue #5 (Upload Authorization Matrix).
"""
import io
import pytest

from app.core.config import settings
from app.integrations import storage
from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio


async def test_farmer_cannot_upload_to_seeds_bucket(client, active_farmer):
    """Farmers must be rejected with 403 when attempting to presign or upload to seeds."""
    resp = await client.post(
        "/api/v1/uploads/presign",
        json={"bucket": "seeds", "file_name": "malicious.jpg", "content_type": "image/jpeg", "size_bytes": 1024},
        headers=auth_headers(active_farmer),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_farmer_cannot_direct_upload_to_seeds_bucket(client, active_farmer):
    file_content = b"\xff\xd8fake-image-bytes"
    resp = await client.post(
        "/api/v1/uploads",
        files={"file": ("seed.jpg", io.BytesIO(file_content), "image/jpeg")},
        data={"bucket": "seeds"},
        headers=auth_headers(active_farmer),
    )
    assert resp.status_code == 403


async def test_manager_can_presign_upload_to_seeds_bucket(client, manager, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_PROVIDER", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "agriflow-test")
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "TEST")
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "TEST")
    storage._s3_client.cache_clear()

    resp = await client.post(
        "/api/v1/uploads/presign",
        json={"bucket": "seeds", "file_name": "seed_catalog.png", "content_type": "image/png", "size_bytes": 2048},
        headers=auth_headers(manager),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "upload_url" in data


async def test_farmer_allowed_to_upload_avatars_and_documents(client, active_farmer, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_PROVIDER", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "agriflow-test")
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "TEST")
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "TEST")
    storage._s3_client.cache_clear()

    resp_avatar = await client.post(
        "/api/v1/uploads/presign",
        json={"bucket": "avatars", "file_name": "photo.jpg", "content_type": "image/jpeg", "size_bytes": 1024},
        headers=auth_headers(active_farmer),
    )
    assert resp_avatar.status_code == 200

    resp_doc = await client.post(
        "/api/v1/uploads/presign",
        json={"bucket": "documents", "file_name": "kyc.pdf", "content_type": "application/pdf", "size_bytes": 2048},
        headers=auth_headers(active_farmer),
    )
    assert resp_doc.status_code == 200


async def test_rejects_unknown_bucket_upload(client, active_farmer):
    resp = await client.post(
        "/api/v1/uploads/presign",
        json={"bucket": "arbitrary_bucket", "file_name": "test.jpg", "content_type": "image/jpeg", "size_bytes": 100},
        headers=auth_headers(active_farmer),
    )
    assert resp.status_code == 422


async def test_path_traversal_in_file_name_rejected(client, active_farmer):
    resp = await client.post(
        "/api/v1/uploads/presign",
        json={"bucket": "documents", "file_name": "../../etc/passwd.pdf", "content_type": "application/pdf", "size_bytes": 100},
        headers=auth_headers(active_farmer),
    )
    assert resp.status_code == 422


async def test_private_kyc_document_uses_presigned_url_not_public(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_PROVIDER", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "agriflow-test")
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "TEST")
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "TEST")
    monkeypatch.setattr(settings, "S3_PUBLIC_BASE_URL", "https://cdn.example.com/")
    storage._s3_client.cache_clear()

    # Public seed object uses CDN
    public_url = storage.read_url("uploads/seed_1.png")
    assert public_url.startswith("https://cdn.example.com/uploads/seed_1.png")

    # Private KYC document MUST NOT use public CDN URL! It must return a presigned S3 URL
    private_url = storage.read_url("farmer-documents/farmer_123/aadhaar.pdf")
    assert not private_url.startswith("https://cdn.example.com")
    assert "X-Amz-Signature" in private_url
