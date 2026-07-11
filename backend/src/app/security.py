"""Security primitives: SSRF guard, rate limiting, security headers.

SSRF: webhook and inbound-router URLs are customer-supplied. Before any
server-side request to them we verify the URL resolves only to public
addresses, so a malicious workspace can't probe the pod network, Cloud SQL,
or the GCP metadata server (169.254.169.254).
"""

import ipaddress
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings


class UnsafeUrlError(ValueError):
    pass


def _is_public(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_url_safe(url: str) -> None:
    """Raise UnsafeUrlError unless `url` is http(s) to a public address."""
    if get_settings().allow_private_webhooks:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"unsupported scheme {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("missing host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve {host}: {exc}") from exc
    for info in infos:
        addr = info[4][0]
        if not _is_public(addr):
            raise UnsafeUrlError(f"{host} resolves to non-public address {addr}")


class RateLimiter:
    """In-memory sliding-window limiter keyed by caller credential.

    Per-process only — good enough for a small replica count; swap for a
    Redis window if limits must be exact across pods.
    """

    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        window = self._hits[key]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True


_EXEMPT_PATHS = ("/healthz", "/metrics", "/internal/")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.limiter = RateLimiter(get_settings().rate_limit_rpm)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not any(path == p or path.startswith(p) for p in _EXEMPT_PATHS):
            # Key by credential when present (fair per-tenant), else client IP.
            auth = request.headers.get("authorization", "")
            key = auth or (request.client.host if request.client else "unknown")
            if not self.limiter.check(key):
                from fastapi.responses import JSONResponse

                return JSONResponse(status_code=429, content={"detail": "Too many requests"})
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


def require_https_url(url: str) -> None:
    """422 helper for endpoints that persist customer webhook URLs."""
    try:
        assert_url_safe(url)
    except UnsafeUrlError as exc:
        raise HTTPException(422, detail=f"Rejected webhook URL: {exc}") from exc
