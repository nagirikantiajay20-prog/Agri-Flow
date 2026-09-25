"""
Redis-backed rate limiting (Master Plan §33). Replaces the client-side
-only limiter found in agriflow-web/src/utils/rateLimiter.js, which is
trivially bypassed since it only runs in the browser.

Buckets (overridable via .env, defaults match the legacy documented
targets in RATE_LIMITING.md — but that doc describes a never-built
Express backend, so these numbers are a starting point to tune against
real traffic, not gospel — Master Plan §1.6):
  - global:  RATE_LIMIT_GLOBAL_PER_15MIN  per signed-in user (per IP when anonymous)
  - login:   RATE_LIMIT_LOGIN_PER_MIN     per IP, on /auth/login
  - auth:    RATE_LIMIT_AUTH_PER_15MIN    per IP, on /auth/*
  - otp:     RATE_LIMIT_OTP_PER_MIN       per phone, on OTP issuance
  - write:   RATE_LIMIT_WRITE_PER_5MIN    per principal, on mutating requests
  - upload:  RATE_LIMIT_UPLOAD_PER_15MIN  per principal, on /uploads/*
  - admin:   RATE_LIMIT_ADMIN_PER_5MIN    per principal, on /admin/*

All applicable buckets for a request are incremented in a single Redis
pipeline: one round trip per request rather than one per bucket.

When Redis is unreachable the shared circuit breaker (app.core.redis)
short-circuits the check instead of letting every request pay a connect
timeout — without that, a Redis outage turns a ~10ms request into a
multi-second one and takes the API down by way of a dependency that is
only supposed to throttle it.
"""
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis, is_available, mark_available, mark_unavailable
from app.core.security import JWTError, decode_token

logger = get_logger(__name__)

GLOBAL_WINDOW = 900
AUTH_WINDOW = 900
UPLOAD_WINDOW = 900
ADMIN_WINDOW = 300
WRITE_WINDOW = 300
LOGIN_WINDOW = 60

EXEMPT_PATHS = ("/health", "/metrics", "/api/v1/docs", "/api/v1/redoc", "/api/v1/openapi.json")


def _principal(request: Request, client_ip: str) -> str:
    """The signed-in user when the request carries a valid access token,
    otherwise the client IP. Keying authenticated traffic on the user
    matters for the mobile app: carrier-grade NAT puts many farmers
    behind one public IP, and an IP-keyed limit would make them share
    one allowance."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        try:
            sub = decode_token(header[7:]).get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass
    return f"ip:{client_ip}"


def _buckets(request: Request, principal: str) -> list[tuple[str, int, int]]:
    """Returns (key, limit, window_seconds) for every bucket this request
    counts against."""
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"

    buckets: list[tuple[str, int, int]] = [
        (f"global:{principal}", settings.RATE_LIMIT_GLOBAL_PER_15MIN, GLOBAL_WINDOW)
    ]
    if path.endswith("/auth/login"):
        buckets.append((f"login:{client_ip}", settings.RATE_LIMIT_LOGIN_PER_MIN, LOGIN_WINDOW))
    if "/auth/" in path:
        buckets.append((f"auth:{client_ip}", settings.RATE_LIMIT_AUTH_PER_15MIN, AUTH_WINDOW))
    if "/uploads" in path:
        buckets.append((f"upload:{principal}", settings.RATE_LIMIT_UPLOAD_PER_15MIN, UPLOAD_WINDOW))
    if "/admin/" in path or "/managers" in path:
        buckets.append((f"admin:{principal}", settings.RATE_LIMIT_ADMIN_PER_5MIN, ADMIN_WINDOW))
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        buckets.append((f"write:{principal}", settings.RATE_LIMIT_WRITE_PER_5MIN, WRITE_WINDOW))
    return buckets


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not getattr(settings, "RATE_LIMIT_ENABLED", False) or request.url.path.startswith(EXEMPT_PATHS):
            return await call_next(request)

        if not is_available():
            if settings.ENVIRONMENT == "production":
                return self._unavailable(request)
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        principal = _principal(request, client_ip)
        buckets = _buckets(request, principal)
        now = int(time.time())

        try:
            redis = get_redis()
            pipe = redis.pipeline(transaction=False)
            for key, _limit, window in buckets:
                redis_key = f"ratelimit:{key}:{now // window}"
                pipe.incr(redis_key)
                pipe.expire(redis_key, window)
            results = await pipe.execute()
            mark_available()
        except Exception as exc:
            mark_unavailable()
            logger.warning("rate_limit_check_failed", error=str(exc))
            if settings.ENVIRONMENT == "production":
                return self._unavailable(request)
            return await call_next(request)

        counts = results[0::2]
        for (key, limit, window), count in zip(buckets, counts, strict=False):
            if count > limit:
                retry_after = window - (now % window)
                logger.info("rate_limited", bucket=key.split(":", 1)[0], path=request.url.path)
                return JSONResponse(
                    status_code=429,
                    content={
                        "success": False,
                        "error": {
                            "code": "RATE_LIMITED",
                            "message": "Too many requests — please slow down",
                            "details": {"retry_after_seconds": retry_after},
                        },
                        "request_id": getattr(request.state, "request_id", None),
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(now + retry_after),
                    },
                )

        response = await call_next(request)
        limit, count = buckets[0][1], counts[0]
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        response.headers["X-RateLimit-Reset"] = str(now + GLOBAL_WINDOW - (now % GLOBAL_WINDOW))
        return response

    @staticmethod
    def _unavailable(request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": {
                    "code": "RATE_LIMITER_UNAVAILABLE",
                    "message": "Service temporarily unavailable",
                    "details": {},
                },
                "request_id": getattr(request.state, "request_id", None),
            },
            headers={"Retry-After": "5"},
        )
