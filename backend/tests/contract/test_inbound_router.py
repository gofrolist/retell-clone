"""Surface 2A — inbound webhook client: strict request shape, response parsing,
and degradation to the DID's default agent on every failure mode."""

import json

import respx
from httpx import Response
from sqlalchemy import update

from app.db import session_factory
from app.models import PhoneNumber
from tests.conftest import AGENT_ID, COMPANION_AGENT_ID, FROM_NUMBER, INTERNAL_HEADERS

ROUTER_URL = "https://consumer.example/functions/v1/inbound-call-router"
CALLER = "+18155141544"


async def _set_inbound_webhook(url: str | None = ROUTER_URL):
    async with session_factory()() as session:
        await session.execute(
            update(PhoneNumber)
            .where(PhoneNumber.phone_number == FROM_NUMBER)
            .values(inbound_webhook_url=url)
        )
        await session.commit()


async def _resolve(client):
    return await client.post(
        "/internal/inbound/resolve",
        headers=INTERNAL_HEADERS,
        json={"from_number": CALLER, "to_number": FROM_NUMBER, "room": "room1"},
    )


@respx.mock
async def test_request_shape_and_override_applied(client):
    await _set_inbound_webhook()
    route = respx.post(ROUTER_URL).mock(
        return_value=Response(
            200,
            json={
                "call_inbound": {
                    "override_agent_id": AGENT_ID,
                    "dynamic_variables": {"first_name": "John", "trial_status": "active"},
                }
            },
        )
    )
    resp = await _resolve(client)
    assert resp.status_code == 200
    cfg = resp.json()
    # webhook received the exact Retell shape
    sent = json.loads(route.calls[0].request.content)
    assert sent == {
        "event": "call_inbound",
        "call_inbound": {"from_number": CALLER, "to_number": FROM_NUMBER},
    }
    # override honored, variables passed through as strings
    assert cfg["agent"]["agent_id"] == AGENT_ID
    assert cfg["dynamic_variables"]["first_name"] == "John"
    assert cfg["direction"] == "inbound"


@respx.mock
async def test_500_degrades_to_default_agent(client):
    await _set_inbound_webhook()
    respx.post(ROUTER_URL).mock(return_value=Response(500, json={"error": "boom"}))
    resp = await _resolve(client)
    assert resp.status_code == 200  # the call must still connect
    assert resp.json()["agent"]["agent_id"] == COMPANION_AGENT_ID


@respx.mock
async def test_malformed_response_degrades_to_default_agent(client):
    await _set_inbound_webhook()
    # old flat format — must NOT be honored (Retell ignored it too)
    respx.post(ROUTER_URL).mock(
        return_value=Response(200, json={"agent_id": AGENT_ID, "retell_llm_dynamic_variables": {}})
    )
    resp = await _resolve(client)
    assert resp.status_code == 200
    assert resp.json()["agent"]["agent_id"] == COMPANION_AGENT_ID


async def test_no_webhook_uses_default_agent(client):
    await _set_inbound_webhook(None)
    resp = await _resolve(client)
    assert resp.status_code == 200
    assert resp.json()["agent"]["agent_id"] == COMPANION_AGENT_ID


@respx.mock
async def test_caller_secret_query_param_when_enabled(client):
    async with session_factory()() as session:
        await session.execute(
            update(PhoneNumber)
            .where(PhoneNumber.phone_number == FROM_NUMBER)
            .values(inbound_webhook_url=ROUTER_URL, inbound_webhook_secret_in_query=True)
        )
        await session.commit()
    route = respx.post(url__startswith=ROUTER_URL).mock(
        return_value=Response(200, json={"call_inbound": {}})
    )
    await _resolve(client)
    assert "caller_secret=test-function-secret" in str(route.calls[0].request.url)
