from pydantic import BaseModel, Field


class PresignUploadRequest(BaseModel):
    bucket: str = Field(description="One of: documents | visits | seeds")
    file_name: str
    content_type: str
    size_bytes: int = Field(gt=0)


class PresignUploadResponse(BaseModel):
    upload_url: str
    object_path: str
    expires_in: int
    method: str = "PUT"


class DirectUploadResponse(BaseModel):
    object_path: str
    public_url: str
    size_bytes: int
    content_type: str
