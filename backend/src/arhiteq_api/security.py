"""Security primitives: SSRF guard, rate limiting, security headers.

SSRF: webhook and inbound-router URLs are customer-supplied. Before any
server-side request to them we verify the URL resolves only to public
addresses, so a malicious workspace can't probe the pod network, Cloud SQL,
or the GCP metadata server (169.254.169.254).
"""

import ipaddress
import logging
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import get_settings

logger = logging.getLogger(__name__)


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
        self._last_sweep = 0.0

    def _sweep(self, now: float) -> None:
        # A flood of distinct credentials (e.g. rotated bogus bearer tokens)
        # would otherwise grow _hits without bound: those keys are never
        # revisited, so their decayed windows are never pruned on access.
        for key in [k for k, w in self._hits.items() if not w or w[-1] <= now - 60]:
            del self._hits[key]
        self._last_sweep = now

    def check(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        if now - self._last_sweep > 60:
            self._sweep(now)
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
        settings = get_settings()
        # Two windows: one per credential (fair per-tenant) and one per client
        # IP that always applies — so an attacker rotating bogus bearer tokens
        # can't mint a fresh bucket per request and bypass the limit.
        self.cred_limiter = RateLimiter(settings.rate_limit_rpm)
        self.ip_limiter = RateLimiter(settings.rate_limit_rpm)
        self.trusted_proxy_count = settings.trusted_proxy_count

    def _client_ip(self, request: Request) -> str:
        if self.trusted_proxy_count > 0:
            forwarded = request.headers.get("x-forwarded-for", "")
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            if hops:
                # Count from the right: the rightmost hop is the nearest proxy.
                return hops[-min(self.trusted_proxy_count, len(hops))]
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not any(path == p or path.startswith(p) for p in _EXEMPT_PATHS):
            ip_ok = self.ip_limiter.check(f"ip:{self._client_ip(request)}")
            auth = request.headers.get("authorization", "")
            cred_ok = self.cred_limiter.check(f"cred:{auth}") if auth else True
            if not (ip_ok and cred_ok):
                from fastapi.responses import JSONResponse

                return JSONResponse(status_code=429, content={"detail": "Too many requests"})
        return await call_next(request)


class UnhandledErrorMiddleware(BaseHTTPMiddleware):
    """Turn any unhandled exception into a JSON 500 *inside* the CORS layer.

    Starlette's built-in ServerErrorMiddleware sits outside every app-added
    middleware, so a 500 it emits never passes back through CORSMiddleware and
    reaches the browser with no Access-Control-Allow-Origin header. The dashboard
    then mislabels a plain server error as "backend unreachable". Catching here —
    where CORS still wraps the response — keeps the CORS headers on error replies.

    HTTPExceptions are already turned into responses by the inner
    ExceptionMiddleware and never reach this layer; only genuinely unhandled
    exceptions do. `except Exception` deliberately lets BaseException (e.g.
    CancelledError) propagate for normal cancellation handling.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})


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
