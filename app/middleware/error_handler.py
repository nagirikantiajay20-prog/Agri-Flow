"""
Wires app.core.exceptions.AppError (and unhandled exceptions) to the
standard error envelope (Master Plan §11/§27/§28) — never a bare HTTP 200
with an error body buried inside, which was confirmed present in the
legacy admin-create-user function for some validation failures.
"""
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _envelope(code: str, message: str, details: dict, request: Request) -> dict:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details},
        "request_id": getattr(request.state, "request_id", None),
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details, request),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # exc.errors() can contain raw exception objects in `ctx` for
        # custom @field_validator errors (e.g. ValueError) — these are
        # not JSON-serializable on their own, hence jsonable_encoder.
        errors = jsonable_encoder(exc.errors())
        for err in errors:
            loc = err.get("loc", ())
            if any(k in loc for k in ("account_number", "password", "token", "aadhaar", "secret")):
                if "input" in err:
                    err["input"] = "[REDACTED]"
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "VALIDATION_ERROR", "Request validation failed",
                {"errors": errors}, request,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.error("unhandled_exception", path=str(request.url), error=str(exc))
        return JSONResponse(
            status_code=500,
            content=_envelope("INTERNAL_ERROR", "An unexpected error occurred", {}, request),
        )
