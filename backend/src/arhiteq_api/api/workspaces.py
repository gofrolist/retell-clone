"""Multi-workspace endpoints for the dashboard (NOT part of the Retell API).

A dashboard user is identified by their Google-verified email and may belong to
several workspaces. The active workspace lives in the session JWT's `ws` claim,
so switching is "re-issue the token" — there is no per-request workspace header
to forge, and every existing workspace-scoping path keeps working untouched.

These handlers deliberately do NOT depend on `require_api_key`: that dependency
resolves a session to the workspace's API key, which fails exactly when we need
these endpoints most (a workspace with no key, or the one just deleted from
under the caller). They authenticate on identity alone.
"""

import logging
from typing import Any, NamedTuple

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import hash_key
from ..config import get_settings
from ..db import get_session
from ..ids import new_api_key
from ..models import ApiKey, Workspace, WorkspaceMember, now_ms
from ..schemas import CompatModel
from ..sessions import decode_session, issue_session
from .auth_google import touch_membership

log = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

# Personas offered by the create-workspace modal. Stored on the workspace as
# `settings.workspace_type`; purely descriptive today.
WORKSPACE_TYPES = ("business", "agency", "developer", "other")


class Identity(NamedTuple):
    """Who is calling, and which workspace they are currently in.

    `email` is None for raw API-key auth: a key carries no personal identity,
    so it can see and create workspaces but has no membership to switch
    between.
    """

    email: str | None
    name: str | None
    workspace_id: str


async def require_identity(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Identity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()

    # Session JWTs contain exactly two dots; try them first so a decode failure
    # falls through to the (indexed) API-key lookup rather than the reverse.
    if token.count(".") == 2:
        claims = decode_session(token)
        if claims is not None:
            return Identity(claims["sub"], claims.get("name"), claims["ws"])

    row = await session.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_key(token), ApiKey.revoked.is_(False))
    )
    if row is not None:
        return Identity(None, None, row.workspace_id)
    raise HTTPException(401, detail="Invalid API key")


def require_session(identity: Identity = Depends(require_identity)) -> Identity:
    """Same, but reject raw API keys — membership is a property of a person."""
    if identity.email is None:
        raise HTTPException(403, detail="Signing in with Google is required to manage workspaces")
    return identity


def _summary(ws: Workspace, role: str, current: str) -> dict[str, Any]:
    return {
        "workspace_id": ws.id,
        "name": ws.name,
        "role": role,
        "created_at_ms": ws.created_at_ms,
        "is_current": ws.id == current,
    }


@router.get("/list-workspaces")
async def list_workspaces(
    identity: Identity = Depends(require_identity),
    session: AsyncSession = Depends(get_session),
):
    """Every workspace the caller can switch into, oldest first.

    The active workspace is always included even without a membership row —
    an allowlisted first login is issued a session before its owner row is
    written, and API-key auth has no membership at all.
    """
    rows: list[tuple[Workspace, str]] = []
    if identity.email is not None:
        rows = list(
            (
                await session.execute(
                    select(Workspace, WorkspaceMember.role)
                    .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                    .where(WorkspaceMember.email == identity.email)
                    .order_by(Workspace.created_at_ms, Workspace.id)
                )
            ).all()
        )
    if not any(ws.id == identity.workspace_id for ws, _ in rows):
        current = await session.get(Workspace, identity.workspace_id)
        if current is not None:
            rows.insert(0, (current, "owner"))
    return [_summary(ws, role, identity.workspace_id) for ws, role in rows]


class CreateWorkspaceRequest(CompatModel):
    name: str = Field(min_length=1, max_length=255)
    workspace_type: str | None = None


@router.post("/create-workspace", status_code=201)
async def create_workspace(
    body: CreateWorkspaceRequest,
    identity: Identity = Depends(require_identity),
    session: AsyncSession = Depends(get_session),
):
    """Create a workspace, make the caller its owner, and switch them into it.

    An API key is minted alongside it: `require_api_key` resolves a dashboard
    session to the workspace's oldest live key, so a keyless workspace would
    403 on every subsequent request.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(422, detail="Workspace name is required")
    if body.workspace_type is not None and body.workspace_type not in WORKSPACE_TYPES:
        raise HTTPException(422, detail=f"workspace_type must be one of {list(WORKSPACE_TYPES)}")

    # Creating a workspace is open to any signed-in user (Retell parity), but
    # each one provisions a live API key and can place billable calls, so a
    # single identity can't mint them without bound. Raw API keys are operator
    # credentials and skip the cap.
    cap = get_settings().max_workspaces_per_user
    if identity.email is not None and cap > 0:
        owned = (
            await session.scalar(
                select(func.count())
                .select_from(WorkspaceMember)
                .where(
                    WorkspaceMember.email == identity.email,
                    WorkspaceMember.role == "owner",
                )
            )
            or 0
        )
        if owned >= cap:
            raise HTTPException(
                409,
                detail=(
                    f"You already own {owned} workspaces (limit {cap}) — "
                    "delete one or ask an operator to raise the limit"
                ),
            )

    ws = Workspace(name=name)
    if body.workspace_type is not None:
        ws.settings = {"workspace_type": body.workspace_type}
    session.add(ws)
    await session.flush()

    secret = new_api_key()
    session.add(
        ApiKey(workspace_id=ws.id, key_hash=hash_key(secret), key_material=secret, name="default")
    )
    if identity.email is not None:
        session.add(
            WorkspaceMember(
                workspace_id=ws.id,
                email=identity.email,
                name=identity.name,
                role="owner",
                last_active_at_ms=now_ms(),
            )
        )
    await session.commit()

    out = _summary(ws, "owner", ws.id)
    if identity.email is None:
        # API-key callers get no session to switch with, so the new key is the
        # only way to reach the workspace they just made. Session callers are
        # handed a token instead — no long-lived secret in a UI response.
        out["api_key"] = secret
    else:
        token, expires_at = issue_session(identity.email, ws.id, identity.name)
        out["token"] = token
        out["expires_at"] = expires_at
    return out


class SwitchWorkspaceRequest(CompatModel):
    workspace_id: str


@router.post("/switch-workspace")
async def switch_workspace(
    body: SwitchWorkspaceRequest,
    identity: Identity = Depends(require_session),
    session: AsyncSession = Depends(get_session),
):
    """Re-issue the session against another workspace the caller belongs to.

    Membership is re-checked here rather than trusted from the token, so a
    removed member can't hop back into a workspace with a stale session. A
    workspace the caller isn't in is indistinguishable from a missing one.
    """
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == body.workspace_id,
            WorkspaceMember.email == identity.email,
        )
    )
    if member is None:
        raise HTTPException(404, detail="Workspace not found")
    ws = await session.get(Workspace, body.workspace_id)
    if ws is None:
        raise HTTPException(404, detail="Workspace not found")

    # Records where to resume on the next sign-in.
    await touch_membership(session, identity.email, ws.id)
    token, expires_at = issue_session(identity.email, ws.id, identity.name)
    return {**_summary(ws, member.role, ws.id), "token": token, "expires_at": expires_at}
