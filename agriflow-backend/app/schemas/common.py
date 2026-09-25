"""
Standard response envelope + pagination (Master Plan §27, §29 / §11).
"""
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    pagination: Pagination


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    message: str = "Success"


class MessageResponse(BaseModel):
    success: bool = True
    message: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
    request_id: str | None = None


class PageParams(BaseModel):
    page: int = 1
    page_size: int = 20
    search: str | None = None
    status: str | None = None
    sort: str = "created_at"
    order: str = "desc"
