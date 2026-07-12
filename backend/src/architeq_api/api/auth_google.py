"""Dashboard login with Google Sign-In (Google Identity Services).

Flow: the dashboard obtains a Google ID token client-side and POSTs it here;
we verify it against Google's certs (audience = our OAuth client id), enforce
the allowlist, and issue an Architeq session JWT the dashboard then sends as
`Authorization: Bearer <session>`.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import Workspace
from ..sessions import decode_session, issue_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    id_token: str


def _email_allowed(email: str) -> bool:
    settings = get_settings()
    allowed_emails = {e.strip().lower() for e in settings.dashboard_allowed_emails if e.strip()}
    allowed_domains = {d.strip().lower() for d in settings.dashboard_allowed_domains if d.strip()}
    email = email.lower()
    if email in allowed_emails:
        return True
    domain = email.rsplit("@", 1)[-1]
    if domain in allowed_domains:
        return True
    # Fail closed: with no allowlist configured, nobody logs in.
    if not allowed_emails and not allowed_domains:
        log.warning("dashboard login rejected: no allowlist configured")
    return False


def verify_google_id_token(token: str) -> dict:
    """Validate signature/expiry/audience with Google's public certs."""
    settings = get_settings()
    if not settings.google_oauth_client_id:
        raise HTTPException(503, detail="Google Sign-In is not configured")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=settings.google_oauth_client_id
        )
    except ValueError as exc:
        raise HTTPException(401, detail="Invalid Google token") from exc
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(401, detail="Invalid Google token issuer")
    if not claims.get("email_verified"):
        raise HTTPException(403, detail="Google account email is not verified")
    return claims


@router.post("/google")
async def google_login(body: GoogleLoginRequest, session: AsyncSession = Depends(get_session)):
    # verify_google_id_token does blocking HTTP (Google cert fetch) via the
    # requests transport; keep it off the event loop.
    claims = await run_in_threadpool(verify_google_id_token, body.id_token)
    email = claims.get("email", "")
    if not _email_allowed(email):
        raise HTTPException(403, detail=f"{email} is not allowed to access this dashboard")

    # Single-tenant deployment: sessions attach to the first workspace.
    workspace = await session.scalar(select(Workspace).order_by(Workspace.created_at_ms).limit(1))
    if workspace is None:
        raise HTTPException(503, detail="No workspace provisioned yet (run architeq_api.seed)")

    token, expires_at = issue_session(email, workspace.id, claims.get("name"))
    return {
        "token": token,
        "expires_at": expires_at,
        "email": email,
        "name": claims.get("name"),
        "picture": claims.get("picture"),
        "workspace_id": workspace.id,
    }


@router.get("/me")
async def me(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail="Missing session")
    claims = decode_session(authorization.removeprefix("Bearer ").strip())
    if claims is None:
        raise HTTPException(401, detail="Invalid or expired session")
    return {
        "email": claims["sub"],
        "name": claims.get("name"),
        "workspace_id": claims["ws"],
        "expires_at": claims["exp"],
    }
