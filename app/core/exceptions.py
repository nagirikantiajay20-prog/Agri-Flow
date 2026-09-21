"""
Domain exceptions + the standard error envelope (Master Plan §11):

    { "success": false, "error": {"code": ..., "message": ..., "details": {}},
      "request_id": "..." }

Replaces the current system's inconsistent error shapes (some Edge
Functions return HTTP 200 with an `{error: "..."}` body instead of a real
4xx/5xx — see Master Plan §28 / §11).
"""
from typing import Any


class AppError(Exception):
    """Base class for all handled application errors."""

    status_code: int = 400
    code: str = "APP_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"


class InvalidStateTransitionError(AppError):
    """E.g. attempting grain_sale pending -> paid without going through
    approved. See Master Plan §23 / §8 / Module 8."""
    status_code = 422
    code = "INVALID_STATE_TRANSITION"


class InsufficientStockError(ConflictError):
    code = "INSUFFICIENT_STOCK"


class CapacityExceededError(ConflictError):
    code = "CAPACITY_EXCEEDED"
