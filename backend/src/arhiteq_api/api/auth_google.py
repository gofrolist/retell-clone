"""Dashboard login with Google Sign-In (Google Identity Services).

Flow: the dashboard obtains a Google ID token client-side and POSTs it here;
we verify it against Google's certs (audience = our OAuth client id), enforce
the allowlist, and issue an Arhiteq session JWT the dashboard then sends as
`Authorization: Bearer <session>`.

Access is granted to: emails on the allowlist (recorded as workspace owners),
existing workspace members, and — via `invite_token` — holders of a pending
invite whose Google-verified email matches the invited one. The invite token
alone is deliberately not enough: like usan-voice-engine, the email match is
the real gate, so a leaked link can't be redeemed by someone else.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import Workspace, WorkspaceInvite, WorkspaceMember, now_ms
from ..sessions import decode_session, issue_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    id_token: str
    invite_token: str | None = None


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


async def touch_membership(session: AsyncSession, email: str, workspace_id: str) -> None:
    """Mark this workspace as the caller's most recent, for the next login.

    A no-op when there's no membership row (raw API keys, and the window
    before an allowlisted bootstrap row is written), so callers don't have to
    check first.
    """
    await session.execute(
        update(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.email == email,
        )
        .values(last_active_at_ms=now_ms())
    )
    await session.commit()


async def _redeem_invite(
    session: AsyncSession, invite_token: str, email: str, name: str | None
) -> str:
    """Validate an invite for the signed-in email and return its workspace id.

    Email mismatch is checked before invite state so a mismatched identity
    learns nothing about the invite. Re-clicking an accepted invite as an
    existing member degrades to a normal login.
    """
    invite = await session.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.token == invite_token)
    )
    if invite is None:
        raise HTTPException(403, detail="Invalid invite link")
    if invite.email != email:
        raise HTTPException(403, detail="This invite was issued to a different email address")
    workspace_id = invite.workspace_id
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.email == email,
        )
    )
    if member is not None:
        return workspace_id
    if invite.status == "revoked":
        raise HTTPException(403, detail="This invite has been revoked")
    if invite.status != "pending" or invite.expires_at_ms <= now_ms():
        raise HTTPException(403, detail="This invite has expired — ask for a new link")

    session.add(
        WorkspaceMember(workspace_id=workspace_id, email=email, name=name, role=invite.role)
    )
    invite.status = "accepted"
    invite.accepted_at_ms = now_ms()
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent redemption (double-click, two tabs): the other request
        # already created the member row — degrade to a normal login.
        await session.rollback()
    return workspace_id


@router.post("/google")
async def google_login(body: GoogleLoginRequest, session: AsyncSession = Depends(get_session)):
    # verify_google_id_token does blocking HTTP (Google cert fetch) via the
    # requests transport; keep it off the event loop.
    claims = await run_in_threadpool(verify_google_id_token, body.id_token)
    email = claims.get("email", "").lower()
    name = claims.get("name")

    workspace_id: str | None
    if body.invite_token:
        workspace_id = await _redeem_invite(session, body.invite_token, email, name)
    else:
        # Users can belong to several workspaces, so a plain login resumes the
        # one they were last in (coalesced: rows predating the column sort as
        # 0, falling back to oldest-membership order).
        member = await session.scalar(
            select(WorkspaceMember)
            .where(WorkspaceMember.email == email)
            .order_by(
                func.coalesce(WorkspaceMember.last_active_at_ms, 0).desc(),
                WorkspaceMember.created_at_ms,
            )
            .limit(1)
        )
        if member is not None:
            workspace_id = member.workspace_id
        elif _email_allowed(email):
            # Bootstrap only: an allowlisted email with no membership anywhere
            # becomes owner of the first workspace, which is how the very
            # first operator gets in on a fresh deployment. It deliberately no
            # longer fires for someone who already has a membership — with a
            # domain allowlist that would silently make every invited employee
            # an owner of workspace #1 on their next sign-in.
            workspace = await session.scalar(
                select(Workspace).order_by(Workspace.created_at_ms).limit(1)
            )
            workspace_id = workspace.id if workspace is not None else None
            if workspace_id is not None:
                session.add(
                    WorkspaceMember(workspace_id=workspace_id, email=email, name=name, role="owner")
                )
                try:
                    await session.commit()
                except IntegrityError:
                    # Concurrent first login already recorded the row.
                    await session.rollback()
        else:
            raise HTTPException(403, detail=f"{email} is not allowed to access this dashboard")
    if workspace_id is None:
        raise HTTPException(503, detail="No workspace provisioned yet (run arhiteq_api.seed)")

    await touch_membership(session, email, workspace_id)
    token, expires_at = issue_session(email, workspace_id, name)
    return {
        "token": token,
        "expires_at": expires_at,
        "email": email,
        "name": name,
        "picture": claims.get("picture"),
        "workspace_id": workspace_id,
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
