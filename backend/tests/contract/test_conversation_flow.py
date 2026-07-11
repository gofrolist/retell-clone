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
