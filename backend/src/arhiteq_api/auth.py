import hashlib
import hmac
import os

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import ApiKey


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def require_api_key(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> ApiKey:
    """Retell-style auth: `Authorization: Bearer <api_key>`.

    Also accepts an Arhiteq dashboard session JWT (Google Sign-In); a valid
    session resolves to the workspace's active API key so every downstream
    workspace-scoping and webhook-signing path behaves identically.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()

    row = await session.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_key(token), ApiKey.revoked.is_(False))
    )
    if row is not None:
        return row

    # Not an API key — try a dashboard session token (JWTs contain two dots).
    if token.count(".") == 2:
        from .sessions import workspace_id_from_session

        workspace_id = workspace_id_from_session(token)
        if workspace_id is not None:
            key_row = await session.scalar(
                select(ApiKey)
                .where(ApiKey.workspace_id == workspace_id, ApiKey.revoked.is_(False))
                .order_by(ApiKey.id)
                .limit(1)
            )
            if key_row is not None:
                return key_row
            raise HTTPException(status_code=403, detail="Workspace has no active API key")

    raise HTTPException(status_code=401, detail="Invalid API key")


async def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ARHITEQ_INTERNAL_TOKEN")
    if not expected:
        # Fail closed: a missing secret must never authenticate anyone. The
        # /internal router hands out the shared function_secret, so a default
        # would be a full worker-plane compromise.
        raise HTTPException(status_code=503, detail="Internal auth not configured")
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="Invalid internal token")
