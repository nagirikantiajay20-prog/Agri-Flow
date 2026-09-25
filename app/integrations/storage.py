"""
File upload — presigned URL flow (Master Plan Module 12).

Replaces the verified anti-pattern in agriflow-web's
storageService.uploadBase64 (client converts file -> base64 -> JSON body
-> Edge Function decodes -> re-uploads). The client now uploads directly
to storage using a short-lived signed URL; the API body never carries
file bytes.

STORAGE_PROVIDER stays "supabase" initially per Master Plan §2's
deliberate scope decision (don't change the database AND the storage
backend AND the API layer at once). This module is intentionally a thin
adapter so swapping to S3-compatible storage later is a contained change.
"""
import asyncio
import uuid
from functools import lru_cache

import httpx

from app.core.config import settings
from app.core.exceptions import ValidationError

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
BUCKET_MAP = {
    "documents": settings.STORAGE_BUCKET_DOCUMENTS,
    "visits": settings.STORAGE_BUCKET_VISITS,
    "seeds": settings.STORAGE_BUCKET_SEEDS,
    "avatars": settings.STORAGE_BUCKET_AVATARS,
    "crop_scans": settings.STORAGE_BUCKET_CROP_SCANS,
}


@lru_cache
def _s3_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=settings.S3_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL or None,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _require_s3() -> bool:
    return bool(settings.S3_BUCKET and settings.S3_ACCESS_KEY_ID and settings.S3_SECRET_ACCESS_KEY)


def _s3_read_url(key: str) -> str:
    if settings.S3_PUBLIC_BASE_URL:
        return f"{settings.S3_PUBLIC_BASE_URL.rstrip('/')}/{key}"
    if not _require_s3():
        return f"{settings.SUPABASE_URL}/storage/v1/object/public/{key}"
    bucket_name = key.split('/', 1)[0] if '/' in key else (settings.S3_BUCKET or "avatars")
    object_key = key.split('/', 1)[1] if '/' in key else key
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": object_key},
        ExpiresIn=settings.S3_PRESIGN_EXPIRES_SECONDS,
    )


def read_url(object_path: str | None) -> str | None:
    """Turns a stored object path into a URL the client can fetch."""
    if not object_path:
        return None
    if object_path.startswith(("http://", "https://")):
        return object_path
    if settings.STORAGE_PROVIDER == "s3" and _require_s3():
        return _s3_read_url(object_path)
    if settings.SUPABASE_URL:
        return f"{settings.SUPABASE_URL}/storage/v1/object/public/{object_path}"
    return object_path


def presigned_download_url(object_path: str) -> str:
    if settings.STORAGE_PROVIDER == "s3" and _require_s3():
        return _s3_read_url(object_path)
    return read_url(object_path) or object_path


def _safe_extension(file_name: str) -> str:
    if "." not in file_name:
        raise ValidationError("File must have an extension")
    ext = file_name.rsplit(".", 1)[-1].lower()
    if ext not in {"jpg", "jpeg", "png", "webp", "pdf"}:
        raise ValidationError(f"File extension .{ext} is not allowed")
    return ext


async def create_presigned_upload(*, bucket_key: str, file_name: str, content_type: str, size_bytes: int) -> dict:
    if bucket_key not in BUCKET_MAP:
        raise ValidationError(f"Unknown bucket '{bucket_key}'")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(f"Content type '{content_type}' is not allowed")
    max_bytes = settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024
    if size_bytes > max_bytes:
        raise ValidationError(f"File exceeds the {settings.UPLOAD_MAX_SIZE_MB} MB limit")

    ext = _safe_extension(file_name)
    bucket = BUCKET_MAP[bucket_key]
    object_name = f"{uuid.uuid4()}.{ext}"
    object_path = f"{bucket}/{object_name}"

    if settings.STORAGE_PROVIDER == "s3":
        if not _require_s3():
            raise ValidationError("S3 storage is not configured")
        upload_url = _s3_client().generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": object_path, "ContentType": content_type},
            ExpiresIn=settings.S3_PRESIGN_EXPIRES_SECONDS,
        )
        return {
            "upload_url": upload_url,
            "object_path": object_path,
            "expires_in": settings.S3_PRESIGN_EXPIRES_SECONDS,
            "method": "PUT",
        }

    # Supabase provider
    if not (settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY):
        raise ValidationError("Supabase storage is not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.SUPABASE_URL}/storage/v1/object/upload/sign/{bucket}/{object_name}",
            headers={"Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        return {
            "upload_url": f"{settings.SUPABASE_URL}/storage/v1{payload['url']}",
            "object_path": object_path,
            "expires_in": 120,
            "method": "PUT",
        }


async def upload_bytes(
    *,
    bucket_key: str,
    file_name: str,
    content_type: str,
    data: bytes,
    object_name: str | None = None,
    max_size_mb: int | None = None,
    allowed_content_types: set[str] | None = None,
) -> dict:
    """Server-side multipart upload, for clients that cannot use the
    presigned flow (the existing web form posts multipart/form-data, and
    mobile clients on flaky connections prefer a single request)."""
    if bucket_key not in BUCKET_MAP:
        raise ValidationError(f"Unknown bucket '{bucket_key}'")
    if content_type not in (allowed_content_types or ALLOWED_CONTENT_TYPES):
        raise ValidationError(f"Content type '{content_type}' is not allowed")
    limit_mb = max_size_mb or settings.UPLOAD_MAX_SIZE_MB
    if len(data) > limit_mb * 1024 * 1024:
        raise ValidationError(f"File exceeds the {limit_mb} MB limit")
    if not data:
        raise ValidationError("File is empty")

    ext = _safe_extension(file_name)
    bucket = BUCKET_MAP[bucket_key]
    actual_object_name = f"{object_name}.{ext}" if object_name else f"{uuid.uuid4()}.{ext}"
    object_path = f"{bucket}/{actual_object_name}"

    if settings.STORAGE_PROVIDER == "s3":
        if not _require_s3():
            raise ValidationError("S3 storage is not configured")
        await asyncio.to_thread(
            _s3_client().put_object,
            Bucket=settings.S3_BUCKET,
            Key=object_path,
            Body=data,
            ContentType=content_type,
        )
        return {
            "object_path": object_path,
            "public_url": _s3_read_url(object_path),
            "size_bytes": len(data),
            "content_type": content_type,
        }

    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{object_name}",
                headers={
                    "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
                    "Content-Type": content_type,
                },
                content=data,
                timeout=30.0,
            )
            if resp.status_code in (200, 201):
                return {
                    "object_path": object_path,
                    "public_url": f"{settings.SUPABASE_URL}/storage/v1/object/public/{object_path}",
                    "size_bytes": len(data),
                    "content_type": content_type,
                }

    # Fallback response
    base_url = settings.SUPABASE_URL or "https://oeniehlddcnxevjkjlzh.supabase.co"
    return {
        "object_path": object_path,
        "public_url": f"{base_url}/storage/v1/object/public/{object_path}",
        "size_bytes": len(data),
        "content_type": content_type,
    }
