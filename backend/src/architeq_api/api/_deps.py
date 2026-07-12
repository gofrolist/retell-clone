"""Shared router helpers: workspace-scoped lookup and PATCH application.

These centralize two patterns that were copy-pasted across every CRUD router,
so workspace scoping can't be forgotten and the mutable-field allowlist stays
the single contract-relevant artifact per router.
"""

from typing import Any, Mapping, TypeVar

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import now_ms

T = TypeVar("T")


async def get_owned(
    session: AsyncSession,
    model: type[T],
    obj_id: str,
    workspace_id: str,
    *,
    detail: str,
    status: int = 404,
) -> T:
    """Fetch `model` by id, scoped to `workspace_id`, or raise `status`.

    A row in another workspace is indistinguishable from a missing one — the
    same status/detail is raised for both, so no cross-tenant existence oracle.
    """
    obj = await session.get(model, obj_id)
    if obj is None or getattr(obj, "workspace_id", None) != workspace_id:
        raise HTTPException(status, detail=detail)
    return obj


def apply_patch(
    obj: Any,
    payload: Mapping[str, Any],
    fields: set[str],
    *,
    bump_version: bool = False,
    touch: bool = False,
) -> None:
    """Copy allowlisted `fields` from `payload` onto `obj` (in place)."""
    for field, value in payload.items():
        if field in fields:
            setattr(obj, field, value)
    if bump_version:
        obj.version += 1
    if touch:
        obj.last_modification_timestamp = now_ms()
