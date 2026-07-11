from tests.conftest import AGENT_ID, AUTH_HEADERS, LLM_ID


async def test_chat_agent_crud_roundtrip(client):
    created = await client.post(
        "/create-chat-agent",
        headers=AUTH_HEADERS,
        json={
            "response_engine": {"type": "retell-llm", "llm_id": LLM_ID},
            "agent_name": "Support Chat",
        },
    )
    assert created.status_code == 201
    body = created.json()
    agent_id = body["agent_id"]
    assert body["agent_type"] == "chat-agent"

    got = await client.get(f"/get-chat-agent/{agent_id}", headers=AUTH_HEADERS)
    assert got.status_code == 200

    listed = await client.get("/list-chat-agents", headers=AUTH_HEADERS)
    assert [a["agent_id"] for a in listed.json()] == [agent_id]

    updated = await client.patch(
        f"/update-chat-agent/{agent_id}", headers=AUTH_HEADERS, json={"agent_name": "Renamed"}
    )
    assert updated.json()["agent_name"] == "Renamed"

    deleted = await client.delete(f"/delete-chat-agent/{agent_id}", headers=AUTH_HEADERS)
    assert deleted.status_code == 204


async def test_voice_agent_is_not_a_chat_agent(client):
    resp = await client.get(f"/get-chat-agent/{AGENT_ID}", headers=AUTH_HEADERS)
    assert resp.status_code == 404
    listed = await client.get("/list-chat-agents", headers=AUTH_HEADERS)
    assert listed.json() == []
