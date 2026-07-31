"""Conversation flow CRUD + list pagination shape."""

from tests.conftest import AUTH_HEADERS

NODES = [
    {
        "id": "start",
        "type": "conversation",
        "instruction": {"type": "prompt", "text": "Greet the customer."},
    }
]


async def _create_flow(client, **overrides):
    payload = {
        "nodes": NODES,
        "start_speaker": "agent",
        "model_choice": {"type": "cascading", "model": "gpt-4.1"},
        "global_prompt": "You are a helpful agent.",
        **overrides,
    }
    resp = await client.post("/create-conversation-flow", headers=AUTH_HEADERS, json=payload)
    assert resp.status_code == 201
    return resp.json()


async def test_create_conversation_flow(client):
    body = await _create_flow(client)
    assert body["conversation_flow_id"].startswith("conversation_flow_")
    assert body["version"] == 0
    assert body["nodes"] == NODES
    assert body["start_node_id"] == "start"  # inferred from first node
    assert body["start_speaker"] == "agent"
    assert body["model_choice"] == {"type": "cascading", "model": "gpt-4.1"}
    assert body["global_prompt"] == "You are a helpful agent."


#: Every optional field of Retell's CreateConversationFlowRequest, with a
#: value distinguishable from the column default. The fixture round-trip in
#: `test_conversation_flow_fidelity.py` only proves the fields our three
#: captures happen to set — `begin_after_user_silence_ms` was in the schema,
#: in none of the captures, and silently dropped by the API for exactly that
#: reason (`CompatModel` sets extra="allow", so the POST still returned 201).
#: This table is the schema-derived complement to that test.
DOCUMENTED_OPTIONAL_FIELDS: dict = {
    "global_prompt": "You are a helpful agent.",
    "flex_mode": True,
    "tools": [{"type": "end_call", "name": "end_call", "tool_id": "tool_1"}],
    "components": [{"name": "shared", "nodes": []}],
    "start_node_id": "start",
    "default_dynamic_variables": {"plan": "gold"},
    "begin_tag_display_position": {"x": 12.5, "y": -4},
    "notes": [
        {
            "id": "note_1",
            "content": "hi",
            "display_position": {"x": 1, "y": 2},
            "size": {"width": 100, "height": 50},
        }
    ],
    "mcps": [{"name": "mcp", "url": "https://example.com/mcp"}],
    "is_transfer_llm": True,
    "model_temperature": 0.4,
    "tool_call_strict_mode": True,
    "knowledge_base_ids": ["kb_1"],
    "kb_config": {"top_k": 3, "filter_score": 0.6},
    "begin_after_user_silence_ms": 4000,
}


async def test_every_documented_field_survives_a_round_trip(client):
    created = await _create_flow(client, **DOCUMENTED_OPTIONAL_FIELDS)
    got = await client.get(
        f"/get-conversation-flow/{created['conversation_flow_id']}", headers=AUTH_HEADERS
    )
    assert got.status_code == 200
    body = got.json()

    dropped = sorted(k for k in DOCUMENTED_OPTIONAL_FIELDS if k not in body)
    assert not dropped, f"fields dropped by the API: {dropped}"
    altered = {k: (v, body[k]) for k, v in DOCUMENTED_OPTIONAL_FIELDS.items() if body[k] != v}
    assert not altered, f"fields altered by the API: {altered}"


async def test_every_documented_field_is_patchable(client):
    """A field the serializer returns but PATCH ignores is just as lossy."""
    flow = await _create_flow(client)
    resp = await client.patch(
        f"/update-conversation-flow/{flow['conversation_flow_id']}",
        headers=AUTH_HEADERS,
        json=DOCUMENTED_OPTIONAL_FIELDS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ignored = {
        k: (v, body.get(k)) for k, v in DOCUMENTED_OPTIONAL_FIELDS.items() if body.get(k) != v
    }
    assert not ignored, f"fields ignored by PATCH: {ignored}"


async def test_get_conversation_flow(client):
    flow = await _create_flow(client)
    got = await client.get(
        f"/get-conversation-flow/{flow['conversation_flow_id']}", headers=AUTH_HEADERS
    )
    assert got.status_code == 200
    assert got.json()["conversation_flow_id"] == flow["conversation_flow_id"]


async def test_list_conversation_flows_paginated_shape(client):
    first = await _create_flow(client)
    second = await _create_flow(client)
    resp = await client.get("/v2/list-conversation-flows", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "pagination_key", "has_more"}
    assert body["has_more"] is False
    assert {f["conversation_flow_id"] for f in body["items"]} == {
        first["conversation_flow_id"],
        second["conversation_flow_id"],
    }

    page = await client.get(
        "/v2/list-conversation-flows", headers=AUTH_HEADERS, params={"limit": 1}
    )
    assert page.status_code == 200
    body = page.json()
    assert len(body["items"]) == 1
    assert body["has_more"] is True
    assert body["pagination_key"] == body["items"][0]["conversation_flow_id"]


async def test_update_conversation_flow_bumps_version(client):
    flow = await _create_flow(client)
    resp = await client.patch(
        f"/update-conversation-flow/{flow['conversation_flow_id']}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "Updated prompt.", "start_node_id": "start"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["global_prompt"] == "Updated prompt."
    assert body["version"] == flow["version"] + 1


async def test_delete_conversation_flow(client):
    flow = await _create_flow(client)
    resp = await client.delete(
        f"/delete-conversation-flow/{flow['conversation_flow_id']}", headers=AUTH_HEADERS
    )
    assert resp.status_code == 204
    got = await client.get(
        f"/get-conversation-flow/{flow['conversation_flow_id']}", headers=AUTH_HEADERS
    )
    assert got.status_code == 404


async def test_agent_stores_conversation_flow_response_engine_verbatim(client):
    flow = await _create_flow(client)
    resp = await client.post(
        "/create-agent",
        headers=AUTH_HEADERS,
        json={
            "voice_id": "cartesia-sonic",
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": flow["conversation_flow_id"],
                "version": 0,
            },
        },
    )
    assert resp.status_code == 201
    engine = resp.json()["response_engine"]
    assert engine["type"] == "conversation-flow"
    assert engine["conversation_flow_id"] == flow["conversation_flow_id"]
