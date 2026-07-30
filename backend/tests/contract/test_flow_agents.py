"""Agents whose response engine is a conversation flow."""

from tests.conftest import AGENT_ID, AUTH_HEADERS, INTERNAL_HEADERS, OTHER_AUTH_HEADERS

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


async def test_update_agent_rejects_non_dict_response_engine(client):
    """A string/list response_engine must 422, not slip through apply_patch and
    later crash _load_flow's `(agent.response_engine or {}).get(...)` with an
    AttributeError (a permanent 500 on the agent's internal config).
    """
    agent = await create_flow_agent(client)
    resp = await client.patch(
        f"/update-agent/{agent['agent_id']}",
        headers=AUTH_HEADERS,
        json={"response_engine": "not-an-object"},
    )
    assert resp.status_code == 422, resp.text

    # The agent must still be usable afterwards: no dangling bad state.
    config = await client.get(f"/get-agent/{agent['agent_id']}", headers=AUTH_HEADERS)
    assert config.status_code == 200, config.text


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


async def test_pinned_call_serves_the_frozen_graph_after_a_rollback(client):
    """The central invariant this branch exists for: a call pinned to a still-
    published version must keep resolving that version's FROZEN graph even
    after the agent has been rolled back and the live rows have moved on.

    Every other rollback test here only asserts the live flow row (e.g.
    test_branching_restores_the_flow_graph); this is the one that asserts the
    thing a running call actually depends on.
    """
    flow = await create_flow(client, global_prompt="ORIGINAL")
    flow_id = flow["conversation_flow_id"]
    agent = await create_flow_agent(client, flow_id=flow_id)
    agent_id = agent["agent_id"]

    v0 = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert v0.status_code == 200, v0.text
    original_version = v0.json()["version"]

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "V2 PROMPT"},
    )
    assert edited.status_code == 200, edited.text

    v1 = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert v1.status_code == 200, v1.text
    v1_version = v1.json()["version"]

    # Pin a call to V1 (see test_published_version_freezes_the_flow for why
    # this route, rather than /internal/agents/{agent_id}/config, exercises
    # resolve_with_flow here).
    call = await client.post(
        "/v2/register-phone-call",
        headers=AUTH_HEADERS,
        json={"agent_id": agent_id, "from_number": "+15551230000", "to_number": "+15559990000"},
    )
    assert call.status_code == 201, call.text
    call_id = call.json()["call_id"]

    # Roll back: branch a new draft from V0. This restores the live flow rows
    # to the original prompt — but must not touch V1's frozen snapshot.
    branched = await client.post(
        f"/create-agent-version/{agent_id}",
        headers=AUTH_HEADERS,
        json={"base_version": original_version},
    )
    assert branched.status_code == 201, branched.text

    live_flow = await client.get(f"/get-conversation-flow/{flow_id}", headers=AUTH_HEADERS)
    assert live_flow.status_code == 200, live_flow.text
    assert live_flow.json()["global_prompt"] == "ORIGINAL", (
        "the branch should have restored the live flow to V0's graph"
    )

    config = await client.get(f"/internal/calls/{call_id}/config", headers=INTERNAL_HEADERS)
    assert config.status_code == 200, config.text
    served = config.json()["conversation_flow"]
    assert served is not None
    assert served["global_prompt"] == "V2 PROMPT", (
        f"call pinned at V{v1_version} served {served['global_prompt']!r} — the "
        "rollback of the live rows leaked into a pinned call instead of the "
        "call resolving V1's frozen snapshot"
    )
    assert served["nodes"] == NODES


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
    flow = await create_flow(client, global_prompt="ORIGINAL")
    flow_id = flow["conversation_flow_id"]
    agent = await create_flow_agent(client, flow_id=flow_id)
    agent_id = agent["agent_id"]

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

    flow_now = await client.get(f"/get-conversation-flow/{flow_id}", headers=AUTH_HEADERS)
    assert flow_now.status_code == 200, flow_now.text
    assert flow_now.json()["global_prompt"] == "ORIGINAL", (
        "branching from the original version left the V2 graph live"
    )


async def test_restore_forks_a_flow_shared_with_another_agent(client):
    """Mirrors test_restore_forks_an_llm_shared_with_another_agent
    (test_agent_versions.py) for conversation flows: two agents share one
    flow, and restoring one must not rewrite the other's graph.
    """
    flow = await create_flow(client, global_prompt="ORIGINAL")
    flow_id = flow["conversation_flow_id"]
    agent_a = await create_flow_agent(client, flow_id=flow_id)
    agent_b = await create_flow_agent(client, flow_id=flow_id)
    agent_a_id = agent_a["agent_id"]
    agent_b_id = agent_b["agent_id"]
    # Both agents start published at V0 (record_initial).

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "V2 PROMPT"},
    )
    assert edited.status_code == 200, edited.text

    # The flow edit opened a draft on both owning agents; publish both so A
    # has something at V0 to branch back to and a V1 to branch away from.
    pub_b = await client.post(f"/publish-agent/{agent_b_id}", headers=AUTH_HEADERS, json={})
    assert pub_b.status_code == 200, pub_b.text
    pub_a = await client.post(f"/publish-agent/{agent_a_id}", headers=AUTH_HEADERS, json={})
    assert pub_a.status_code == 200, pub_a.text

    branched = await client.post(
        f"/create-agent-version/{agent_a_id}", headers=AUTH_HEADERS, json={"base_version": 0}
    )
    assert branched.status_code == 201, branched.text

    # B was never touched by the branch: it still points at the shared flow,
    # and that flow still carries the edited content.
    agent_b_now = (await client.get(f"/get-agent/{agent_b_id}", headers=AUTH_HEADERS)).json()
    assert agent_b_now["response_engine"]["conversation_flow_id"] == flow_id
    shared = await client.get(f"/get-conversation-flow/{flow_id}", headers=AUTH_HEADERS)
    assert shared.status_code == 200, shared.text
    assert shared.json()["global_prompt"] == "V2 PROMPT"

    # A got a private copy carrying the restored (original) graph.
    agent_a_now = (await client.get(f"/get-agent/{agent_a_id}", headers=AUTH_HEADERS)).json()
    forked_id = agent_a_now["response_engine"]["conversation_flow_id"]
    assert forked_id != flow_id
    forked = await client.get(f"/get-conversation-flow/{forked_id}", headers=AUTH_HEADERS)
    assert forked.status_code == 200, forked.text
    assert forked.json()["global_prompt"] == "ORIGINAL"
    assert forked.json()["nodes"] == NODES


async def test_get_agent_version_returns_the_frozen_graph(client):
    """The dashboard's version route must freeze the graph like the worker's.

    `test_published_version_freezes_the_flow` covers /internal/calls/.../config.
    This covers /get-agent-version, which the editor reads when you select an
    older version — it used to call `versions.resolve()` (no flow at all), so
    the canvas would render TODAY's graph under a "Viewing V1 — published
    versions are immutable" banner.
    """
    flow = await create_flow(client, global_prompt="ORIGINAL")
    flow_id = flow["conversation_flow_id"]
    agent = await create_flow_agent(client, flow_id=flow_id)
    agent_id = agent["agent_id"]

    published = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert published.status_code == 200, published.text
    version = published.json()["version"]

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "REWRITTEN AFTER PUBLISH"},
    )
    assert edited.status_code == 200, edited.text

    got = await client.get(f"/get-agent-version/{agent_id}/{version}", headers=AUTH_HEADERS)
    assert got.status_code == 200, got.text
    served = got.json()["conversation_flow"]
    assert served is not None
    assert served["global_prompt"] == "ORIGINAL"
    assert served["nodes"] == NODES


async def test_get_agent_version_flow_is_null_for_a_prompt_agent(client):
    """The key is always present, so the client never feature-detects."""
    got = await client.get(f"/get-agent-version/{AGENT_ID}/0", headers=AUTH_HEADERS)
    assert got.status_code == 200, got.text
    assert got.json()["conversation_flow"] is None
    assert got.json()["response_engine_config"] is not None


async def test_discard_restores_the_flow_graph(client):
    """Dropping an open draft on a flow-backed agent must restore the graph
    of the version the live rows fall back to.
    """
    flow = await create_flow(client, global_prompt="ORIGINAL")
    flow_id = flow["conversation_flow_id"]
    agent = await create_flow_agent(client, flow_id=flow_id)
    agent_id = agent["agent_id"]
    # Fresh agent starts published at V0 (record_initial).

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "DRAFT PROMPT"},
    )
    assert edited.status_code == 200, edited.text

    resp = await client.delete(f"/delete-agent-version/{agent_id}/1", headers=AUTH_HEADERS)
    assert resp.status_code == 204, resp.text

    flow_now = await client.get(f"/get-conversation-flow/{flow_id}", headers=AUTH_HEADERS)
    assert flow_now.status_code == 200, flow_now.text
    assert flow_now.json()["global_prompt"] == "ORIGINAL", (
        "discarding the draft did not restore the flow graph"
    )
    assert flow_now.json()["nodes"] == NODES
