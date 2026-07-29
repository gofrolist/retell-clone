"""Agents whose response engine is a conversation flow."""

from tests.conftest import AUTH_HEADERS, OTHER_AUTH_HEADERS

NODES = [
    {
        "id": "start",
        "type": "conversation",
        "instruction": {"type": "prompt", "text": "Greet the caller."},
        "edges": [
            {
                "id": "edge-1",
                "transition_condition": {"type": "prompt", "prompt": "Done"},
                "destination_node_id": "bye",
            }
        ],
    },
    {
        "id": "bye",
        "type": "end",
        "instruction": {"type": "static_text", "text": "Goodbye."},
    },
]


async def create_flow(client, headers=AUTH_HEADERS, **overrides):
    resp = await client.post(
        "/create-conversation-flow",
        headers=headers,
        json={"nodes": NODES, "start_speaker": "agent", **overrides},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_flow_agent(client, flow_id=None, headers=AUTH_HEADERS):
    if flow_id is None:
        flow_id = (await create_flow(client, headers))["conversation_flow_id"]
    resp = await client.post(
        "/create-agent",
        headers=headers,
        json={
            "agent_name": "Flow Agent",
            "voice_id": "cartesia-sonic-english",
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": flow_id,
            },
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def test_create_agent_with_conversation_flow(client):
    agent = await create_flow_agent(client)
    assert agent["response_engine"]["type"] == "conversation-flow"
    assert agent["response_engine"]["conversation_flow_id"].startswith("conversation_flow_")


async def test_create_agent_rejects_unknown_flow(client):
    resp = await client.post(
        "/create-agent",
        headers=AUTH_HEADERS,
        json={
            "agent_name": "Bad",
            "voice_id": "cartesia-sonic-english",
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": "conversation_flow_does_not_exist",
            },
        },
    )
    assert resp.status_code == 404, resp.text


async def test_update_agent_rejects_another_workspaces_flow(client, other_workspace):
    agent = await create_flow_agent(client)
    foreign = await create_flow(client, headers=OTHER_AUTH_HEADERS)
    resp = await client.patch(
        f"/update-agent/{agent['agent_id']}",
        headers=AUTH_HEADERS,
        json={
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": foreign["conversation_flow_id"],
            }
        },
    )
    assert resp.status_code == 404, resp.text


async def test_create_agent_rejects_another_workspaces_flow(client, other_workspace):
    foreign = await create_flow(client, headers=OTHER_AUTH_HEADERS)
    resp = await client.post(
        "/create-agent",
        headers=AUTH_HEADERS,
        json={
            "agent_name": "Cross tenant",
            "voice_id": "cartesia-sonic-english",
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": foreign["conversation_flow_id"],
            },
        },
    )
    assert resp.status_code == 404, resp.text
