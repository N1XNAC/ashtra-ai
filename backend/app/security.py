"""Essential API safety: rate limiting, API-key gate, security headers.

No extra dependencies — in-memory sliding windows (single-process dev box).
For multi-worker/prod, swap _hit() backend for Redis.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings

_windows: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()
WINDOW_S = 60.0


def _hit(key: str, limit: int) -> tuple[bool, float]:
    """Sliding-window hit. Returns (allowed, retry_after_seconds)."""
    now = time.monotonic()
    with _lock:
        q = _windows[key]
        while q and q[0] <= now - WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return False, max(0.0, q[0] + WINDOW_S - now)
        q.append(now)
        return True, 0.0


def check_rate(key: str, limit: int):
    allowed, retry_after = _hit(key, limit)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Slow down, master.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


def client_ip(req: Request) -> str:
    # Local dev: direct connection. If ever behind a proxy, trust only
    # X-Forwarded-For from known proxies (not implemented — localhost box).
    return req.client.host if req.client else "unknown"


# Paths that never consume budget and never need a key (liveness only).
OPEN_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path not in OPEN_PATHS:
            allowed, retry_after = _hit(
                f"ip:{client_ip(request)}", settings.rate_limit_per_minute)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Slow down, master."},
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )
        return await call_next(request)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Shared-secret gate. Inactive while API_KEY is empty (localhost dev)."""

    async def dispatch(self, request: Request, call_next):
        if settings.api_key and request.url.path not in OPEN_PATHS:
            # Constant-time compare to avoid timing leaks.
            import hmac
            got = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(got, settings.api_key):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid API key."},
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return resp
