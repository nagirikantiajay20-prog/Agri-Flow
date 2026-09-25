
from fastapi import APIRouter, File, Form, UploadFile

from app.core.dependencies import ActiveUser
from app.integrations import storage
from app.schemas.common import SuccessResponse
from app.schemas.upload import DirectUploadResponse, PresignUploadRequest, PresignUploadResponse

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/presign", response_model=SuccessResponse[PresignUploadResponse])
async def presign_upload(body: PresignUploadRequest, user: ActiveUser):
    # Replaces the base64-through-JSON anti-pattern verified in
    # agriflow-web's storageService.uploadBase64 (Master Plan Module 12):
    # the client uploads directly to storage using this signed URL, the
    # API body never carries file bytes.
    result = await storage.create_presigned_upload(
        bucket_key=body.bucket, file_name=body.file_name, content_type=body.content_type, size_bytes=body.size_bytes
    )
    return SuccessResponse(data=PresignUploadResponse(**result))


@router.post("", response_model=SuccessResponse[DirectUploadResponse], status_code=201)
async def upload_file(
    user: ActiveUser,
    file: UploadFile = File(...),
    bucket: str = Form("documents"),
):
    result = await storage.upload_bytes(
        bucket_key=bucket,
        file_name=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        data=await file.read(),
    )
    return SuccessResponse(data=DirectUploadResponse(**result))
