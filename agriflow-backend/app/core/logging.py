"""
Structured logging. Never logs password, JWT, OTP, bank account, or secret
key values (Master Plan §49) — request/response logging middleware must
redact these fields before this is called.
"""
import logging
import sys

import structlog

_REDACT_KEYS = {
    "password", "password_hash", "otp", "jwt", "token", "access_token",
    "refresh_token", "account_number", "ifsc_code", "upi_id", "secret",
    "authorization", "jwt_secret_key", "supabase_service_role_key",
}


def _redact_processor(_, __, event_dict):
    for key in list(event_dict.keys()):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = "***REDACTED***"
    return event_dict


def configure_logging(environment: str = "development") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO if environment != "development" else logging.DEBUG,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer()
            if environment == "production"
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
