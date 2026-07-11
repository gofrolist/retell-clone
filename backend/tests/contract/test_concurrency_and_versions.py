"""GET /get-concurrency, GET /get-agent-versions, POST /publish-agent."""

import architeq_api.db as db_module
from architeq_api.models import Call
from tests.conftest import AGENT_ID, AUTH_HEADERS, WORKSPACE_ID


async def test_get_concurrency_shape_and_zero_baseline(client):
    resp = await client.get("/get-concurrency", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_concurrency"] == 0
    assert body["concurrency_limit"] == 20
    assert body["base_concurrency"] == 20
    assert body["purchased_concurrency"] == 0
    assert body["concurrency_purchase_limit"] == 100


async def test_get_concurrency_counts_ongoing_calls(client):
    async with db_module.session_factory()() as session:
        session.add(
            Call(
                workspace_id=WORKSPACE_ID,
                agent_id=AGENT_ID,
                direction="outbound",
                call_status="ongoing",
            )
        )
        session.add(
            Call(
                workspace_id=WORKSPACE_ID,
                agent_id=AGENT_ID,
                direction="outbound",
                call_status="ended",
            )
        )
        await session.commit()
    resp = await client.get("/get-concurrency", headers=AUTH_HEADERS)
    assert resp.json()["current_concurrency"] == 1


async def test_get_concurrency_requires_auth(client):
    assert (await client.get("/get-concurrency")).status_code == 401


async def test_get_agent_versions_returns_current_version_list(client):
    resp = await client.get(f"/get-agent-versions/{AGENT_ID}", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    versions = resp.json()
    assert isinstance(versions, list) and len(versions) == 1
    assert versions[0]["agent_id"] == AGENT_ID
    assert versions[0]["version"] == 0
    assert versions[0]["is_published"] is True


async def test_get_agent_versions_unknown_agent_404(client):
    resp = await client.get("/get-agent-versions/agent_nope", headers=AUTH_HEADERS)
    assert resp.status_code == 404


async def test_publish_agent_returns_published_agent(client):
    resp = await client.post(f"/publish-agent/{AGENT_ID}", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == AGENT_ID
    assert body["is_published"] is True


async def test_publish_agent_unknown_agent_404(client):
    resp = await client.post("/publish-agent/agent_nope", headers=AUTH_HEADERS)
    assert resp.status_code == 404
