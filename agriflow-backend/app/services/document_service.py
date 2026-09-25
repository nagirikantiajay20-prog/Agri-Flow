"""
Farmer KYC documents and profile avatar.

Only the storage object path is persisted — never a URL. Readable links
are minted at response time by app.integrations.storage.read_url, which
for private buckets means a short-lived signed URL. A leaked API response
or database row therefore never contains a permanent link to a farmer's
Aadhaar card or bank passbook.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.integrations import storage
from app.models.enums import DocumentType
from app.models.user import FarmerDocument, FarmerProfile, User
from app.services import audit_service

IMAGE_TYPES = {"image/jpeg", "image/png"}
DOCUMENT_TYPES = {"image/jpeg", "image/png", "application/pdf"}

PROFILE_COLUMN: dict[DocumentType, str] = {
    DocumentType.AVATAR: "profile_photo",
    DocumentType.AADHAAR: "aadhaar_card_url",
    DocumentType.PASSBOOK: "bank_passbook_url",
    DocumentType.LAND: "land_ownership_url",
}


def upload_rules(doc_type: DocumentType) -> tuple[str, set[str], int]:
    """(storage area, allowed MIME types, max size in MB) for a document type."""
    if doc_type == DocumentType.AVATAR:
        return "avatars", IMAGE_TYPES, getattr(settings, "UPLOAD_MAX_AVATAR_MB", 2)
    return "documents", DOCUMENT_TYPES, getattr(settings, "UPLOAD_MAX_DOCUMENT_MB", 5)


async def upload_document(
    db: AsyncSession,
    *,
    farmer: User,
    doc_type: DocumentType,
    file_name: str,
    content_type: str,
    data: bytes,
) -> tuple[FarmerDocument, str | None]:
    profile = (
        await db.execute(select(FarmerProfile).where(FarmerProfile.user_id == farmer.id))
    ).scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Farmer profile not found")

    area, allowed, max_mb = upload_rules(doc_type)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    stored = await storage.upload_bytes(
        bucket_key=area,
        file_name=file_name,
        content_type=content_type,
        data=data,
        object_name=f"farmer_{farmer.id}/{doc_type.value}_{stamp}",
        max_size_mb=max_mb,
        allowed_content_types=allowed,
    )

    document = FarmerDocument(
        farmer_id=farmer.id,
        document_type=doc_type,
        object_path=stored["object_path"],
        content_type=content_type,
        size_bytes=stored["size_bytes"],
    )
    db.add(document)
    setattr(profile, PROFILE_COLUMN[doc_type], stored["object_path"])
    await db.flush()

    await audit_service.record(
        db, actor_id=farmer.id, action="document.upload", entity_type="farmer_document",
        entity_id=document.id, new_value={"document_type": doc_type.value},
    )
    return document, storage.read_url(stored["object_path"])
