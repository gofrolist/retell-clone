"""Surface 2A — inbound webhook client: strict request shape, response parsing,
and degradation to the DID's default agent on every failure mode."""

import json

import respx
from httpx import Response
from sqlalchemy import update

from arhiteq_api.db import session_factory
from arhiteq_api.models import Contact, PhoneNumber
from tests.conftest import (
    AGENT_ID,
    COMPANION_AGENT_ID,
    FROM_NUMBER,
    INTERNAL_HEADERS,
    WORKSPACE_ID,
)

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


async def _add_contact(
    phone: str = CALLER,
    first_name: str = "Evgenii",
    last_name: str = "Vasilenko",
    timezone: str | None = None,
    workspace_id: str = WORKSPACE_ID,
):
    async with session_factory()() as session:
        session.add(
            Contact(
                workspace_id=workspace_id,
                phone_number=phone,
                first_name=first_name,
                last_name=last_name,
                timezone=timezone,
            )
        )
        await session.commit()


async def test_contact_fills_name_variables_without_webhook(client):
    await _set_inbound_webhook(None)
    await _add_contact()
    resp = await _resolve(client)
    assert resp.status_code == 200
    dyn = resp.json()["dynamic_variables"]
    assert dyn["first_name"] == "Evgenii"
    assert dyn["last_name"] == "Vasilenko"


async def test_contact_timezone_becomes_user_timezone(client):
    await _set_inbound_webhook(None)
    await _add_contact(timezone="America/New_York")
    resp = await _resolve(client)
    dyn = resp.json()["dynamic_variables"]
    assert dyn["user_timezone"] == "America/New_York"


async def test_user_timezone_defaults_when_contact_has_none(client):
    await _set_inbound_webhook(None)
    await _add_contact()
    resp = await _resolve(client)
    assert resp.json()["dynamic_variables"]["user_timezone"] == "America/Los_Angeles"


async def test_phone_defaults_to_caller_id(client):
    await _set_inbound_webhook(None)
    resp = await _resolve(client)
    assert resp.json()["dynamic_variables"]["phone"] == CALLER


@respx.mock
async def test_webhook_phone_overrides_caller_id_default(client):
    await _set_inbound_webhook()
    respx.post(ROUTER_URL).mock(
        return_value=Response(
            200,
            json={"call_inbound": {"dynamic_variables": {"phone": "+10000000000"}}},
        )
    )
    resp = await _resolve(client)
    assert resp.json()["dynamic_variables"]["phone"] == "+10000000000"


@respx.mock
async def test_empty_webhook_value_does_not_erase_contact_name(client):
    await _set_inbound_webhook()
    await _add_contact()
    # Dispatchers send first_name:"" for nameless leads — the contact's name
    # must survive, but empty values for unknown keys pass through verbatim.
    respx.post(ROUTER_URL).mock(
        return_value=Response(
            200,
            json={"call_inbound": {"dynamic_variables": {"first_name": "", "state": ""}}},
        )
    )
    resp = await _resolve(client)
    dyn = resp.json()["dynamic_variables"]
    assert dyn["first_name"] == "Evgenii"
    assert dyn["state"] == ""


@respx.mock
async def test_webhook_variables_win_over_contact(client):
    await _set_inbound_webhook()
    await _add_contact()
    respx.post(ROUTER_URL).mock(
        return_value=Response(
            200,
            json={"call_inbound": {"dynamic_variables": {"first_name": "John"}}},
        )
    )
    resp = await _resolve(client)
    dyn = resp.json()["dynamic_variables"]
    assert dyn["first_name"] == "John"  # webhook wins
    assert dyn["last_name"] == "Vasilenko"  # contact fills the gap


async def test_contact_matches_non_e164_stored_number(client):
    await _set_inbound_webhook(None)
    # NANP number saved without the +1 country code still matches the caller
    await _add_contact(phone=CALLER.removeprefix("+1"))
    resp = await _resolve(client)
    assert resp.json()["dynamic_variables"]["first_name"] == "Evgenii"


async def test_contact_in_other_workspace_is_ignored(client, other_workspace):
    await _set_inbound_webhook(None)
    await _add_contact(workspace_id=other_workspace)
    resp = await _resolve(client)
    assert "first_name" not in resp.json()["dynamic_variables"]


async def test_empty_contact_names_are_omitted(client):
    await _set_inbound_webhook(None)
    await _add_contact(first_name="", last_name="")
    resp = await _resolve(client)
    dyn = resp.json()["dynamic_variables"]
    assert "first_name" not in dyn
    assert "last_name" not in dyn


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
