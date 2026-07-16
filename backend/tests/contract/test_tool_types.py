"""Retell tool-type contract: every general_tools type round-trips verbatim.

The backend stores tool declarations as opaque JSON (never validates or
renames fields) so a Retell export imports unchanged. This pins the full
Retell tool union — including types the worker does not execute yet
(code, mcp, bridge_transfer, cancel_transfer), which must still persist.
"""

import arhiteq_api.db as db_module
from arhiteq_api.models import Call
from tests.conftest import AGENT_ID, AUTH_HEADERS, INTERNAL_HEADERS, WORKSPACE_ID


async def _seed_call(workspace_id: str = WORKSPACE_ID) -> str:
    async with db_module.session_factory()() as session:
        call = Call(
            workspace_id=workspace_id,
            agent_id=AGENT_ID,
            direction="outbound",
            call_status="ongoing",
        )
        session.add(call)
        await session.commit()
        return call.call_id


ALL_RETELL_TOOLS = [
    {"type": "end_call", "name": "end_call", "description": "End the call."},
    {
        "type": "transfer_call",
        "name": "transfer_call",
        "transfer_destination": {"type": "predefined", "number": "+14155550123"},
        "transfer_option": {"type": "cold_transfer"},
    },
    {
        "type": "check_availability_cal",
        "name": "check_availability",
        "cal_api_key": "cal_live_key",
        "event_type_id": 12345,
        "timezone": "America/Los_Angeles",
    },
    {
        "type": "book_appointment_cal",
        "name": "book_appointment",
        "cal_api_key": "cal_live_key",
        "event_type_id": 12345,
        "timezone": "America/Los_Angeles",
    },
    {"type": "press_digit", "name": "press_digit", "delay_ms": 900},
    {
        "type": "send_sms",
        "name": "send_sms",
        "sms_content": {"type": "inferred", "prompt": "Text the booking summary."},
    },
    {
        "type": "extract_dynamic_variable",
        "name": "extract_user_info",
        "description": "Extract user info.",
        "variables": [
            {"name": "plan", "type": "enum", "description": "Plan", "choices": ["a", "b"]},
            {"name": "age", "type": "number", "description": "Age"},
        ],
    },
    {
        "type": "agent_swap",
        "name": "swap_to_support",
        "agent_id": "agent_support0000000000000000001",
        "post_call_analysis_setting": "both_agents",
    },
    {
        "type": "custom",
        "name": "lookup_order",
        "url": "https://example.com/tools/lookup",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
        "speak_during_execution": True,
        "speak_after_execution": True,
    },
    {"type": "code", "name": "compute_quote", "code": "return 1 + 1;"},
    {"type": "mcp", "name": "mcp_search", "description": "Search via MCP.", "mcp_id": "mcp_1"},
    {"type": "bridge_transfer", "name": "bridge_transfer"},
    {"type": "cancel_transfer", "name": "cancel_transfer"},
]


class TestToolTypeRoundTrip:
    async def test_all_tool_types_round_trip_verbatim(self, client):
        created = await client.post(
            "/create-retell-llm",
            headers=AUTH_HEADERS,
            json={"general_prompt": "You are helpful.", "general_tools": ALL_RETELL_TOOLS},
        )
        assert created.status_code == 201
        llm_id = created.json()["llm_id"]

        got = await client.get(f"/get-retell-llm/{llm_id}", headers=AUTH_HEADERS)
        assert got.status_code == 200
        assert got.json()["general_tools"] == ALL_RETELL_TOOLS

    async def test_states_tools_round_trip_verbatim(self, client):
        states = [
            {
                "name": "intake",
                "state_prompt": "Collect info.",
                "tools": [ALL_RETELL_TOOLS[4], ALL_RETELL_TOOLS[6]],
                "edges": [{"destination_state_name": "booking", "description": "ready"}],
            },
            {"name": "booking", "state_prompt": "Book it.", "tools": [ALL_RETELL_TOOLS[3]]},
        ]
        created = await client.post(
            "/create-retell-llm",
            headers=AUTH_HEADERS,
            json={"general_prompt": "p", "states": states, "starting_state": "intake"},
        )
        assert created.status_code == 201
        got = await client.get(f"/get-retell-llm/{created.json()['llm_id']}", headers=AUTH_HEADERS)
        assert got.json()["states"] == states


class TestInternalAgentConfig:
    async def test_agent_swap_config_shape(self, client):
        call_id = await _seed_call()
        resp = await client.get(
            f"/internal/agents/{AGENT_ID}/config",
            params={"call_id": call_id},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent"]["agent_id"] == AGENT_ID
        assert body["llm"] is not None and "general_prompt" in body["llm"]

    async def test_unknown_agent_404(self, client):
        call_id = await _seed_call()
        resp = await client.get(
            "/internal/agents/agent_missing000000000000000000/config",
            params={"call_id": call_id},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 404

    async def test_cross_workspace_agent_404(self, client, other_workspace):
        # The destination agent must live in the calling call's workspace:
        # agent_id comes from user-editable tool config, so an unscoped
        # lookup would leak another tenant's prompt and tool secrets.
        call_id = await _seed_call(workspace_id=other_workspace)
        resp = await client.get(
            f"/internal/agents/{AGENT_ID}/config",
            params={"call_id": call_id},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 404

    async def test_unknown_call_404(self, client):
        resp = await client.get(
            f"/internal/agents/{AGENT_ID}/config",
            params={"call_id": "call_missing"},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 404

    async def test_requires_internal_token(self, client):
        resp = await client.get(f"/internal/agents/{AGENT_ID}/config", params={"call_id": "call_x"})
        assert resp.status_code in (401, 403)
