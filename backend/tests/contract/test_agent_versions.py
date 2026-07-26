"""Agent version history: drafts, immutable published snapshots, resolution.

The load-bearing property is the last group: an open draft must never reach a
live call. See docs/AGENT_VERSIONING.md.
"""

import arhiteq_api.db as db_module
from arhiteq_api.models import Agent, Call
from tests.conftest import AGENT_ID, AUTH_HEADERS, FROM_NUMBER, LLM_ID, WORKSPACE_ID


async def _versions(client, agent_id=AGENT_ID):
    resp = await client.get(f"/get-agent-versions/{agent_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    return resp.json()


async def _agent(client, agent_id=AGENT_ID, **params):
    resp = await client.get(f"/get-agent/{agent_id}", headers=AUTH_HEADERS, params=params)
    assert resp.status_code == 200
    return resp.json()


# ------------------------------------------------------------------ seeding


async def test_agent_predating_versioning_is_seeded_as_published(client):
    # The conftest agents are inserted straight into the table, like every row
    # that existed before this feature shipped.
    versions = await _versions(client)
    assert len(versions) == 1
    assert versions[0]["version"] == 0
    assert versions[0]["is_published"] is True
    assert versions[0]["is_live"] is True
    assert versions[0]["base_version"] is None


async def test_created_agent_starts_at_published_v0(client):
    created = await client.post(
        "/create-agent",
        headers=AUTH_HEADERS,
        json={
            "response_engine": {"type": "retell-llm", "llm_id": LLM_ID},
            "voice_id": "cartesia-sonic",
        },
    )
    agent_id = created.json()["agent_id"]
    versions = await _versions(client, agent_id)
    assert [(v["version"], v["is_published"]) for v in versions] == [(0, True)]


# -------------------------------------------------------------------- drafts


async def test_first_edit_opens_a_draft(client):
    updated = await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "Renamed"}
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 1
    assert updated.json()["is_published"] is False

    versions = await _versions(client)
    assert [(v["version"], v["is_published"]) for v in versions] == [(1, False), (0, True)]
    assert versions[0]["base_version"] == 0
    # The published version still names the old config; the draft carries the edit.
    assert versions[0]["agent_name"] == "Renamed"
    assert versions[1]["agent_name"] == "Sales"
    assert versions[1]["is_live"] is True


async def test_further_edits_reuse_the_open_draft(client):
    for name in ("One", "Two", "Three"):
        await client.patch(
            f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": name}
        )
    versions = await _versions(client)
    assert [v["version"] for v in versions] == [1, 0]


async def test_prompt_edit_opens_the_owning_agents_draft(client):
    resp = await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "New prompt"}
    )
    assert resp.status_code == 200
    versions = await _versions(client)
    assert [(v["version"], v["is_published"]) for v in versions] == [(1, False), (0, True)]


async def test_folder_move_does_not_open_a_draft(client):
    folder = await client.post(
        "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "Ops"}
    )
    await client.patch(
        f"/update-agent/{AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"folder_id": folder.json()["folder_id"]},
    )
    assert [v["version"] for v in await _versions(client)] == [0]


async def test_editing_an_older_version_is_rejected(client):
    await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "Draft"}
    )
    resp = await client.patch(
        f"/update-agent/{AGENT_ID}",
        headers=AUTH_HEADERS,
        params={"version": 0},
        json={"agent_name": "Rewriting history"},
    )
    assert resp.status_code == 422
    assert "not editable" in resp.json()["detail"]


# ----------------------------------------------------------------- publishing


async def test_publish_freezes_the_draft_and_makes_it_live(client):
    await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "Renamed"}
    )
    resp = await client.post(
        f"/publish-agent/{AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"version_title": "Rename", "version_description": "Renamed the agent"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_published"] is True

    versions = await _versions(client)
    assert [(v["version"], v["is_published"], v["is_live"]) for v in versions] == [
        (1, True, True),
        (0, True, False),
    ]
    assert versions[0]["version_title"] == "Rename"
    assert versions[0]["version_description"] == "Renamed the agent"
    assert versions[0]["published_timestamp"] is not None


async def test_published_version_keeps_its_config_after_later_edits(client):
    await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "V1 prompt"}
    )
    await client.post(f"/publish-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={})
    await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "V2 prompt"}
    )

    async with db_module.session_factory()() as session:
        from arhiteq_api.services import versions as versions_svc

        agent = await session.get(Agent, AGENT_ID)
        _, llm_v1, _ = await versions_svc.resolve(session, agent, 1)
        _, llm_v2, _ = await versions_svc.resolve(session, agent, versions_svc.LATEST)
        assert llm_v1.general_prompt == "V1 prompt"
        assert llm_v2.general_prompt == "V2 prompt"


async def test_publish_specific_version_rolls_back_without_new_versions(client):
    await client.patch(f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "V1"})
    await client.post(f"/publish-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={})
    resp = await client.post(
        f"/publish-agent-version/{AGENT_ID}", headers=AUTH_HEADERS, json={"version": 0}
    )
    assert resp.status_code == 200

    versions = await _versions(client)
    assert [v["version"] for v in versions] == [1, 0]
    assert [v["is_live"] for v in versions] == [False, True]
    # The editor still shows the latest; calls go back to V0.
    assert (await _agent(client))["agent_name"] == "V1"
    assert (await _agent(client, version="latest_published"))["agent_name"] == "Sales"


async def test_single_version_fetch_carries_its_prompt(client):
    await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "V1 prompt"}
    )
    await client.post(f"/publish-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={})
    await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "V2 prompt"}
    )

    v0 = await client.get(f"/get-agent-version/{AGENT_ID}/0", headers=AUTH_HEADERS)
    v1 = await client.get(f"/get-agent-version/{AGENT_ID}/1", headers=AUTH_HEADERS)
    v2 = await client.get(f"/get-agent-version/{AGENT_ID}/2", headers=AUTH_HEADERS)
    assert v0.status_code == 200
    assert v0.json()["response_engine_config"]["general_prompt"].startswith("You are Clara")
    assert v1.json()["response_engine_config"]["general_prompt"] == "V1 prompt"
    # The draft's content is the live rows.
    assert v2.json()["response_engine_config"]["general_prompt"] == "V2 prompt"
    assert v2.json()["is_published"] is False

    assert (
        await client.get(f"/get-agent-version/{AGENT_ID}/9", headers=AUTH_HEADERS)
    ).status_code == 404


async def test_publish_unknown_version_404s(client):
    resp = await client.post(
        f"/publish-agent-version/{AGENT_ID}", headers=AUTH_HEADERS, json={"version": 99}
    )
    assert resp.status_code == 404


# ------------------------------------------------------- branching / discard


async def test_branch_from_an_old_version_restores_its_config(client):
    await client.patch(f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "V1"})
    await client.post(f"/publish-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={})

    resp = await client.post(
        f"/create-agent-version/{AGENT_ID}", headers=AUTH_HEADERS, json={"base_version": 0}
    )
    assert resp.status_code == 201
    assert resp.json()["version"] == 2
    assert resp.json()["base_version"] == 0
    # The editor now shows V0's config, as a fresh draft.
    assert (await _agent(client))["agent_name"] == "Sales"
    assert [v["version"] for v in await _versions(client)] == [2, 1, 0]


async def test_branching_while_a_draft_is_open_409s(client):
    await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "Draft"}
    )
    resp = await client.post(
        f"/create-agent-version/{AGENT_ID}", headers=AUTH_HEADERS, json={"base_version": 0}
    )
    assert resp.status_code == 409
    assert "V1" in resp.json()["detail"]


async def test_discarding_a_draft_restores_the_live_config(client):
    await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "Oops"}
    )
    resp = await client.delete(f"/delete-agent-version/{AGENT_ID}/1", headers=AUTH_HEADERS)
    assert resp.status_code == 204

    assert [v["version"] for v in await _versions(client)] == [0]
    agent = await _agent(client)
    assert agent["agent_name"] == "Sales"
    assert agent["version"] == 0
    assert agent["is_published"] is True


async def test_discarding_a_branched_draft_falls_back_to_the_newest_version(client):
    # V0 -> V1 published -> draft branched from V0. Discarding it must land on
    # V1 (the newest version left), not on the V0 it branched from.
    await client.patch(f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "V1"})
    await client.post(f"/publish-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={})
    await client.post(
        f"/create-agent-version/{AGENT_ID}", headers=AUTH_HEADERS, json={"base_version": 0}
    )
    assert (await _agent(client))["agent_name"] == "Sales"

    resp = await client.delete(f"/delete-agent-version/{AGENT_ID}/2", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    agent = await _agent(client)
    assert agent["version"] == 1
    assert agent["agent_name"] == "V1"


async def test_published_versions_cannot_be_deleted(client):
    resp = await client.delete(f"/delete-agent-version/{AGENT_ID}/0", headers=AUTH_HEADERS)
    assert resp.status_code == 422


# ------------------------------------------------- calls resolve the publish


async def test_call_runs_the_published_version_not_the_open_draft(client):
    await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "DRAFT ONLY"}
    )
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": "+15557654321"},
    )
    assert resp.status_code == 201
    call_id = resp.json()["call_id"]
    assert resp.json()["agent_version"] == 0

    config = await client.get(
        f"/internal/calls/{call_id}/config", headers={"X-Internal-Token": "test-internal-token"}
    )
    assert config.json()["llm"]["general_prompt"] != "DRAFT ONLY"


async def test_call_config_stays_pinned_across_a_publish(client):
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": "+15557654321"},
    )
    call_id = resp.json()["call_id"]
    await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "AFTER"}
    )
    async with db_module.session_factory()() as session:
        call = await session.get(Call, call_id)
        agent_id = call.agent_id
    await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})

    config = await client.get(
        f"/internal/calls/{call_id}/config", headers={"X-Internal-Token": "test-internal-token"}
    )
    assert config.json()["llm"]["general_prompt"] != "AFTER"


async def test_override_agent_version_pins_the_call(client):
    await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"general_prompt": "V1 prompt"}
    )
    async with db_module.session_factory()() as session:
        agent = await session.get(Agent, AGENT_ID)
        companion_id = agent.agent_id
    await client.post(f"/publish-agent/{companion_id}", headers=AUTH_HEADERS, json={})

    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "to_number": "+15557654321",
            "override_agent_id": AGENT_ID,
            "override_agent_version": 0,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["agent_version"] == 0

    config = await client.get(
        f"/internal/calls/{resp.json()['call_id']}/config",
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert config.json()["llm"]["general_prompt"] == "You are Clara. Caller: {{first_name}}."


async def test_inbound_call_resolves_the_published_version(client):
    await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"agent_name": "Draft name"}
    )
    resp = await client.post(
        "/internal/inbound/resolve",
        headers={"X-Internal-Token": "test-internal-token"},
        json={"from_number": "+15551234567", "to_number": FROM_NUMBER},
    )
    assert resp.status_code == 200
    assert resp.json()["agent"]["is_published"] is True


# ------------------------------------------------------------------ scoping


async def test_version_endpoints_are_workspace_scoped(client, other_workspace):
    from tests.conftest import OTHER_AUTH_HEADERS

    assert (
        await client.get(f"/get-agent-versions/{AGENT_ID}", headers=OTHER_AUTH_HEADERS)
    ).status_code == 404
    assert (
        await client.post(
            f"/create-agent-version/{AGENT_ID}",
            headers=OTHER_AUTH_HEADERS,
            json={"base_version": 0},
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/publish-agent-version/{AGENT_ID}", headers=OTHER_AUTH_HEADERS, json={"version": 0}
        )
    ).status_code == 404


async def test_deleting_an_agent_deletes_its_versions(client):
    from sqlalchemy import select

    from arhiteq_api.models import AgentVersion

    created = await client.post(
        "/create-agent",
        headers=AUTH_HEADERS,
        json={
            "response_engine": {"type": "retell-llm", "llm_id": LLM_ID},
            "voice_id": "cartesia-sonic",
        },
    )
    agent_id = created.json()["agent_id"]
    assert (
        await client.delete(f"/delete-agent/{agent_id}", headers=AUTH_HEADERS)
    ).status_code == 204
    async with db_module.session_factory()() as session:
        rows = (
            await session.scalars(select(AgentVersion).where(AgentVersion.agent_id == agent_id))
        ).all()
    assert rows == []
    assert WORKSPACE_ID  # imported for symmetry with the other scoping tests
