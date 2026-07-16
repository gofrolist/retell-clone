from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, PhoneNumber
from ..schemas import (
    CreatePhoneNumberRequest,
    ImportPhoneNumberRequest,
    phone_number_to_dict,
)
from ._deps import apply_patch, get_owned

router = APIRouter(tags=["phone-numbers"])

_MUTABLE_FIELDS = {
    "nickname",
    "inbound_agent_id",
    "outbound_agent_id",
    "inbound_webhook_url",
    "inbound_webhook_secret_in_query",
}


def _pretty(e164: str) -> str:
    if e164.startswith("+1") and len(e164) == 12:
        return f"+1({e164[2:5]}){e164[5:8]}-{e164[8:]}"
    return e164


@router.post("/create-phone-number", status_code=201)
async def create_phone_number(
    body: CreatePhoneNumberRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    # Number purchase goes through Telnyx; in dev the number is passed in
    # directly. Purchase flow: TODO(telnyx) — order via Telnyx Numbers API.
    if not body.phone_number:
        raise HTTPException(422, detail="phone_number required (Telnyx purchase not configured)")
    return await _create(body.phone_number, body, api_key, session, provider="telnyx")


@router.post("/import-phone-number", status_code=201)
async def import_phone_number(
    body: ImportPhoneNumberRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    return await _create(body.phone_number, body, api_key, session, provider="custom")


async def _create(e164, body, api_key, session, provider):
    if await session.get(PhoneNumber, e164) is not None:
        raise HTTPException(409, detail="phone_number already exists")
    pn = PhoneNumber(
        phone_number=e164,
        phone_number_pretty=_pretty(e164),
        workspace_id=api_key.workspace_id,
        provider=provider,
        nickname=body.nickname,
        inbound_agent_id=body.inbound_agent_id,
        outbound_agent_id=body.outbound_agent_id,
        inbound_webhook_url=body.inbound_webhook_url,
        area_code=getattr(body, "area_code", None),
    )
    session.add(pn)
    await session.commit()
    return phone_number_to_dict(pn)


@router.get("/get-phone-number/{phone_number}")
async def get_phone_number(
    phone_number: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    pn = await get_owned(
        session, PhoneNumber, phone_number, api_key.workspace_id, detail="Phone number not found"
    )
    return phone_number_to_dict(pn)


@router.get("/list-phone-numbers")
async def list_phone_numbers(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(PhoneNumber).where(PhoneNumber.workspace_id == api_key.workspace_id)
        )
    ).all()
    return [phone_number_to_dict(p) for p in rows]


@router.patch("/update-phone-number/{phone_number}")
async def update_phone_number(
    phone_number: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    pn = await get_owned(
        session, PhoneNumber, phone_number, api_key.workspace_id, detail="Phone number not found"
    )
    payload = await request.json()
    apply_patch(pn, payload, _MUTABLE_FIELDS, touch=True)
    await session.commit()
    return phone_number_to_dict(pn)


@router.delete("/delete-phone-number/{phone_number}", status_code=204)
async def delete_phone_number(
    phone_number: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    pn = await get_owned(
        session, PhoneNumber, phone_number, api_key.workspace_id, detail="Phone number not found"
    )
    await session.delete(pn)
    await session.commit()
