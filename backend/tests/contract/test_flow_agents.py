"""Agents whose response engine is a conversation flow."""

from tests.conftest import AUTH_HEADERS, INTERNAL_HEADERS, OTHER_AUTH_HEADERS

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


async def test_published_version_freezes_the_flow(client):
    """Editing a draft flow must not change what a published version serves."""
    flow = await create_flow(client, global_prompt="ORIGINAL")
    flow_id = flow["conversation_flow_id"]
    agent = await create_flow_agent(client, flow_id=flow_id)
    agent_id = agent["agent_id"]

    published = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert published.status_code == 200, published.text
    version = published.json()["version"]

    # Pin a call to the published version before the flow is edited, then read
    # its config back through the same route the worker uses
    # (/internal/agents/{agent_id}/config needs a call_id to scope its
    # cross-tenant check — see api/internal.py:get_agent_config — so this
    # exercises resolve_with_flow through the plain call-config path instead,
    # matching the pattern in test_agent_versions.py).
    call = await client.post(
        "/v2/register-phone-call",
        headers=AUTH_HEADERS,
        json={"agent_id": agent_id, "from_number": "+15551230000", "to_number": "+15559990000"},
    )
    assert call.status_code == 201, call.text
    call_id = call.json()["call_id"]

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "REWRITTEN AFTER PUBLISH"},
    )
    assert edited.status_code == 200, edited.text

    config = await client.get(f"/internal/calls/{call_id}/config", headers=INTERNAL_HEADERS)
    assert config.status_code == 200, config.text
    served = config.json()["conversation_flow"]
    assert served is not None
    assert served["global_prompt"] == "ORIGINAL", (
        f"published version {version} served {served['global_prompt']!r} "
        "instead of the frozen graph"
    )
    assert served["nodes"] == NODES, f"published version {version} did not freeze the graph's nodes"


async def test_flow_edit_opens_the_owning_agents_draft(client):
    """PATCHing a flow must open a draft on every agent whose response_engine
    points at it — mirroring update-retell-llm's agents_using_llm/touch dance
    (api/llms.py) — or a flow edit can never be published and reach a call.
    """
    agent = await create_flow_agent(client)
    agent_id = agent["agent_id"]
    flow_id = agent["response_engine"]["conversation_flow_id"]

    published = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert published.status_code == 200, published.text

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "EDITED"},
    )
    assert edited.status_code == 200, edited.text

    versions_resp = await client.get(f"/get-agent-versions/{agent_id}", headers=AUTH_HEADERS)
    assert versions_resp.status_code == 200, versions_resp.text
    versions = versions_resp.json()
    assert [(v["version"], v["is_published"]) for v in versions] == [(1, False), (0, True)], (
        "flow edit did not open a draft on the owning agent"
    )

    republish = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert republish.status_code == 200, republish.text
    assert republish.json()["version"] == 1, republish.text

    call = await client.post(
        "/v2/register-phone-call",
        headers=AUTH_HEADERS,
        json={"agent_id": agent_id, "from_number": "+15551230000", "to_number": "+15559990000"},
    )
    assert call.status_code == 201, call.text
    call_id = call.json()["call_id"]

    config = await client.get(f"/internal/calls/{call_id}/config", headers=INTERNAL_HEADERS)
    assert config.status_code == 200, config.text
    served = config.json()["conversation_flow"]
    assert served is not None
    assert served["global_prompt"] == "EDITED", (
        "publishing after a flow edit did not serve the edited graph"
    )


async def test_deleted_flow_still_serves_its_published_snapshot(client):
    """Deleting a flow must not blank out a published version's graph: the
    complete config is sitting in flow_snapshot and resolve_with_flow must
    rebuild it rather than serve null.
    """
    agent = await create_flow_agent(client)
    agent_id = agent["agent_id"]
    flow_id = agent["response_engine"]["conversation_flow_id"]

    published = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert published.status_code == 200, published.text

    call = await client.post(
        "/v2/register-phone-call",
        headers=AUTH_HEADERS,
        json={"agent_id": agent_id, "from_number": "+15551230000", "to_number": "+15559990000"},
    )
    assert call.status_code == 201, call.text
    call_id = call.json()["call_id"]

    deleted = await client.delete(f"/delete-conversation-flow/{flow_id}", headers=AUTH_HEADERS)
    assert deleted.status_code == 204, deleted.text

    config = await client.get(f"/internal/calls/{call_id}/config", headers=INTERNAL_HEADERS)
    assert config.status_code == 200, config.text
    served = config.json()["conversation_flow"]
    assert served is not None, "deleted flow served null instead of the frozen snapshot"
    assert served["nodes"] == NODES
    assert served["conversation_flow_id"] == flow_id
    assert isinstance(served["version"], int), "rebuilt flow left version null"
    assert isinstance(served["last_modification_timestamp"], int), (
        "rebuilt flow left last_modification_timestamp null"
    )


async def test_branching_restores_the_flow_graph(client):
    """Rolling back to an old version must roll the graph back with it."""
    agent = await create_flow_agent(client)
    agent_id = agent["agent_id"]
    flow_id = agent["response_engine"]["conversation_flow_id"]

    first = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert first.status_code == 200, first.text
    original_version = first.json()["version"]

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "V2 PROMPT"},
    )
    assert edited.status_code == 200, edited.text
    second = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert second.status_code == 200, second.text

    branched = await client.post(
        f"/create-agent-version/{agent_id}",
        headers=AUTH_HEADERS,
        json={"base_version": original_version},
    )
    assert branched.status_code in (200, 201), branched.text

    flow = await client.get(f"/get-conversation-flow/{flow_id}", headers=AUTH_HEADERS)
    assert flow.status_code == 200, flow.text
    assert flow.json()["global_prompt"] != "V2 PROMPT", (
        "branching from the original version left the V2 graph live"
    )
