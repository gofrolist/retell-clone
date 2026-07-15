"""Chat surface: create-chat, get-chat, list-chat, create-chat-completion,
end-chat."""

from tests.conftest import AGENT_ID, AUTH_HEADERS


async def _create_chat(client, **overrides):
    resp = await client.post(
        "/create-chat", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID, **overrides}
    )
    assert resp.status_code == 201
    return resp.json()


async def test_create_chat(client):
    body = await _create_chat(
        client,
        metadata={"customer_id": "user123"},
        retell_llm_dynamic_variables={"customer_name": "John Doe"},
    )
    assert body["chat_id"].startswith("chat_")
    assert body["agent_id"] == AGENT_ID
    assert body["chat_status"] == "ongoing"
    assert body["metadata"] == {"customer_id": "user123"}
    assert body["retell_llm_dynamic_variables"] == {"customer_name": "John Doe"}
    assert body["message_with_tool_calls"] == []


def test_chat_prompt_resolves_system_and_user_variables():
    from architeq_api.api.chats import _resolve_chat_prompt
    from architeq_api.models import Chat

    chat = Chat(
        chat_id="chat_abc123",
        retell_llm_dynamic_variables={"customer_name": "John", "session_type": "override"},
    )
    prompt = "id={{chat_id}} type={{session_type}} name={{customer_name}} keep={{unknown}}"
    assert _resolve_chat_prompt(prompt, chat) == (
        "id=chat_abc123 type=override name=John keep={{unknown}}"
    )


async def test_create_chat_unknown_agent_is_non_2xx(client):
    resp = await client.post("/create-chat", headers=AUTH_HEADERS, json={"agent_id": "agent_nope"})
    assert resp.status_code == 422


async def test_chat_completion_appends_user_and_agent_messages(client):
    chat = await _create_chat(client)
    resp = await client.post(
        "/create-chat-completion",
        headers=AUTH_HEADERS,
        json={"chat_id": chat["chat_id"], "content": "hi how are you doing?"},
    )
    assert resp.status_code == 201
    messages = resp.json()["messages"]
    # Only the newly generated agent messages come back, not the user input.
    assert len(messages) == 1
    agent_msg = messages[0]
    assert agent_msg["role"] == "agent"
    assert agent_msg["message_id"].startswith("msg_")
    assert agent_msg["content"]  # canned reply without ARCHITEQ_GOOGLE_API_KEY

    got = (await client.get(f"/get-chat/{chat['chat_id']}", headers=AUTH_HEADERS)).json()
    history = got["message_with_tool_calls"]
    assert [m["role"] for m in history] == ["user", "agent"]
    assert history[0]["content"] == "hi how are you doing?"
    assert "User: hi how are you doing?" in got["transcript"]


async def test_chat_completion_unknown_chat_404(client):
    resp = await client.post(
        "/create-chat-completion",
        headers=AUTH_HEADERS,
        json={"chat_id": "chat_missing", "content": "hello"},
    )
    assert resp.status_code == 404


async def test_list_chat(client):
    first = await _create_chat(client)
    second = await _create_chat(client)
    resp = await client.get("/list-chat", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    ids = {c["chat_id"] for c in resp.json()}
    assert ids == {first["chat_id"], second["chat_id"]}


async def test_end_chat_204_and_blocks_further_completions(client):
    chat = await _create_chat(client)
    resp = await client.patch(f"/end-chat/{chat['chat_id']}", headers=AUTH_HEADERS)
    assert resp.status_code == 204

    got = (await client.get(f"/get-chat/{chat['chat_id']}", headers=AUTH_HEADERS)).json()
    assert got["chat_status"] == "ended"
    assert got["end_timestamp"] >= got["start_timestamp"]

    blocked = await client.post(
        "/create-chat-completion",
        headers=AUTH_HEADERS,
        json={"chat_id": chat["chat_id"], "content": "still there?"},
    )
    assert blocked.status_code == 422


async def test_chat_requires_auth(client):
    assert (await client.post("/create-chat", json={"agent_id": AGENT_ID})).status_code == 401
    assert (await client.get("/list-chat")).status_code == 401
