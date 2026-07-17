"""Regression: a 500 must still carry CORS headers.

Starlette's built-in ServerErrorMiddleware sits outside every app-added
middleware, so an unhandled exception would reach the browser without an
Access-Control-Allow-Origin header and the dashboard would mislabel a plain
server error as "backend unreachable". UnhandledErrorMiddleware catches the
exception *inside* the CORS layer so the error reply keeps its CORS headers.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from arhiteq_api.security import UnhandledErrorMiddleware

ORIGIN = "https://dashboard.arhiteq.com"


def _app_with_wiring() -> FastAPI:
    app = FastAPI()
    # Same order as main.py: UnhandledError added first (innermost), CORS last
    # (outermost) so it wraps the error response on the way out.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_methods=["*"])

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    return app


async def test_unhandled_error_keeps_cors_headers():
    transport = ASGITransport(app=_app_with_wiring())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom", headers={"Origin": ORIGIN})

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    # The crucial bit: the browser only trusts the response if this header is set.
    assert resp.headers["access-control-allow-origin"] == ORIGIN
