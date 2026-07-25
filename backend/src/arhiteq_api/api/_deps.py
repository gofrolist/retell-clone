"""Shared router helpers: workspace-scoped lookup, PATCH application, paging.

These centralize the patterns that were copy-pasted across every CRUD router,
so workspace scoping can't be forgotten, the mutable-field allowlist stays
the single contract-relevant artifact per router, and keyset pagination has one
implementation of its (timestamp, id) tie-break.
"""

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import now_ms


async def get_owned[T](
    session: AsyncSession,
    model: type[T],
    obj_id: Any,
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
    """Copy allowlisted `fields` from `payload` onto `obj` (in place).

    An explicit null aimed at a non-nullable column resets it to the column
    default (Retell semantics: null clears the field) instead of writing a
    NULL that would either IntegrityError or corrupt the wire contract.
    """
    # PATCH handlers hand us `await request.json()` unvalidated, so a body that
    # is a JSON array or string reaches here. Reject it as a 422 rather than
    # letting .items() raise AttributeError into an opaque 500.
    if not isinstance(payload, Mapping):
        raise HTTPException(422, detail="Request body must be a JSON object")
    for field, value in payload.items():
        if field not in fields:
            continue
        if value is None:
            column = obj.__table__.columns.get(field)
            if column is not None and not column.nullable:
                default = column.default
                if default is None or not default.is_scalar:
                    continue  # no scalar default to reset to: leave unchanged
                value = default.arg
        setattr(obj, field, value)
    if bump_version:
        obj.version += 1
    if touch:
        obj.last_modification_timestamp = now_ms()


async def apply_keyset_page(
    session: AsyncSession,
    query: Any,
    model: type[Any],
    ts_col: Any,
    id_col: Any,
    *,
    pagination_key: str | None,
    ascending: bool,
) -> Any:
    """Anchor `query` on `pagination_key` and order it deterministically.

    Ordering and paging both key on (timestamp, id): create-batch-call inserts
    many rows in the same millisecond, so a timestamp-only anchor would skip
    every sibling of the anchor row. Callers keep their own limit/has_more and
    response shaping — those differ on the wire and must not be unified here.
    """
    if pagination_key:
        anchor = await session.get(model, pagination_key)
        if anchor is not None:
            key = tuple_(ts_col, id_col)
            bound = (getattr(anchor, ts_col.key), getattr(anchor, id_col.key))
            query = query.where(key > bound if ascending else key < bound)
    if ascending:
        return query.order_by(ts_col.asc(), id_col.asc())
    return query.order_by(ts_col.desc(), id_col.desc())
