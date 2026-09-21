from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import paginated
from app.core.database import get_db
from app.core.dependencies import Pagination, require_farmer
from app.models.enums import DocumentType
from app.models.user import User
from app.schemas import farmer_app as s
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.services import document_service, ledger_service

router = APIRouter(tags=["farmer · ledger & documents"])


@router.get("/transactions", response_model=PaginatedResponse[s.TransactionOut])
async def list_transactions(
    params: Pagination,
    farmer: Annotated[User, Depends(require_farmer("transaction.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    txns, total = await ledger_service.list_transactions_for_actor(db, actor=farmer, params=params)
    return paginated([s.TransactionOut.model_validate(t, from_attributes=True) for t in txns], total, params)


@router.post("/documents/upload", response_model=SuccessResponse[s.DocumentUploadOut], status_code=201)
async def upload_document(
    farmer: Annotated[User, Depends(require_farmer("document.upload.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    doc_type: DocumentType = Form(...),
):
    document, url = await document_service.upload_document(
        db,
        farmer=farmer,
        doc_type=doc_type,
        file_name=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        data=await file.read(),
    )
    return SuccessResponse(
        data=s.DocumentUploadOut(
            document_id=document.id,
            document_type=document.document_type,
            url=url,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
        ),
        message="Document uploaded",
    )
