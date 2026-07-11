"""Architeq dashboard sessions: short-lived HS256 JWTs.

Issued by /auth/google after verifying a Google ID token; accepted by
`require_api_key` as an alternative to a workspace API key.
"""

import logging
import time
from typing import Any

import jwt

from .config import get_settings

log = logging.getLogger(__name__)

_ALGO = "HS256"
_ISSUER = "architeq"


def _secret() -> str:
    secret = get_settings().session_secret
    if not secret:
        raise RuntimeError("ARCHITEQ_SESSION_SECRET is not configured")
    return secret


def issue_session(email: str, workspace_id: str, name: str | None = None) -> tuple[str, int]:
    now = int(time.time())
    exp = now + get_settings().session_ttl_seconds
    token = jwt.encode(
        {
            "iss": _ISSUER,
            "sub": email,
            "name": name,
            "ws": workspace_id,
            "iat": now,
            "exp": exp,
        },
        _secret(),
        algorithm=_ALGO,
    )
    return token, exp


def decode_session(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGO], issuer=_ISSUER)
    except jwt.InvalidTokenError:
        return None
    except RuntimeError:
        log.warning("session token presented but ARCHITEQ_SESSION_SECRET unset")
        return None


def workspace_id_from_session(token: str) -> str | None:
    claims = decode_session(token)
    return claims.get("ws") if claims else None
