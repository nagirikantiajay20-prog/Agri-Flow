import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.admin import TransactionPayRequest, TransactionPublic
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.services import ledger_service

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=PaginatedResponse[TransactionPublic])
async def list_transactions(
    params: Pagination,
    actor: Annotated[object, Depends(require_permission("transaction.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # NOTE: this deliberately has no POST — see
    # app.services.ledger_service module docstring. Rows are created only
    # as a side effect of seed purchases / grain sale payments.
    txns, total = await ledger_service.list_transactions_for_actor(db, actor=actor, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[TransactionPublic.model_validate(t) for t in txns],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@router.patch("/{transaction_id}/pay", response_model=SuccessResponse[TransactionPublic])
async def pay_transaction(
    transaction_id: uuid.UUID,
    body: TransactionPayRequest,
    actor: Annotated[object, Depends(require_permission("transaction.pay"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    txn = await ledger_service.mark_transaction_paid(
        db, actor=actor, transaction_id=transaction_id, description=body.description
    )
    return SuccessResponse(data=TransactionPublic.model_validate(txn))
