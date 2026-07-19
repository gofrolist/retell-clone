"""Outbound dialing via LiveKit SIP (Telnyx trunk) + agent job dispatch."""

import json
import logging

from ..config import get_settings
from ..models import Call

log = logging.getLogger(__name__)

AGENT_NAME = "arhiteq-agent"


def room_name(call: Call) -> str:
    return f"call_{call.call_id}"


def _lk_client():
    """Build a LiveKitAPI client from settings (caller owns closing it)."""
    settings = get_settings()
    from livekit import api as lk_api

    return lk_api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )


async def _create_dispatch(lk, call: Call) -> None:
    """Create the agent job dispatch for the call's room on an open client."""
    from livekit import api as lk_api

    await lk.agent_dispatch.create_dispatch(
        lk_api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME,
            room=room_name(call),
            metadata=json.dumps({"call_id": call.call_id}),
        )
    )


async def dispatch_agent(call: Call) -> None:
    """Create the agent job dispatch for the call's room.

    The worker (agent_name=AGENT_NAME) joins the room and fetches the call
    config through the internal API using the call_id in the metadata.
    Raises on failure — callers must not report the call as started.
    """
    lk = _lk_client()
    try:
        await _create_dispatch(lk, call)
    finally:
        await lk.aclose()


async def start_outbound_call(call: Call) -> None:
    """Create the room, dispatch the agent job, and dial the callee.

    Raises on failure — create-phone-call must return non-2xx if the call
    could not be initiated (consumers mark the lead `retell_error` on non-2xx).
    """
    settings = get_settings()
    from livekit import api as lk_api

    room = room_name(call)
    lk = _lk_client()
    try:
        await _create_dispatch(lk, call)
        await lk.sip.create_sip_participant(
            lk_api.CreateSIPParticipantRequest(
                sip_trunk_id=settings.sip_outbound_trunk_id,
                sip_call_to=call.to_number,
                sip_number=call.from_number,
                room_name=room,
                participant_identity=f"pstn_{call.to_number}",
                wait_until_answered=False,
                headers=call.custom_sip_headers or {},
            )
        )
    finally:
        await lk.aclose()
    log.info("dialing %s -> %s in room %s", call.from_number, call.to_number, room)
