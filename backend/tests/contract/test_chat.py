"""Chat surface: create-chat, get-chat, list-chat, create-chat-completion,
end-chat."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from arhiteq_api.config import Settings
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
    from arhiteq_api.api.chats import _resolve_chat_prompt
    from arhiteq_api.models import Chat, now_ms

    chat = Chat(
        chat_id="chat_abc123",
        start_timestamp=now_ms() - 90_000,
        retell_llm_dynamic_variables={"customer_name": "John", "session_type": "override"},
    )
    # Whitespace inside braces resolves, same as the worker's resolver.
    prompt = "id={{ chat_id }} type={{session_type}} name={{customer_name}} keep={{unknown}}"
    assert _resolve_chat_prompt(prompt, chat) == (
        "id=chat_abc123 type=override name=John keep={{unknown}}"
    )
    # Time/session system variables resolve on the chat channel too.
    resolved = _resolve_chat_prompt("{{current_time}} | {{session_duration}}", chat)
    assert "{{current_time}}" not in resolved
    assert "{{session_duration}}" not in resolved
    assert "1 minute 3" in resolved  # ~90s elapsed, tolerant of test runtime


async def test_chat_completion_uses_vertex_client_when_configured(client, monkeypatch):
    # In Vertex mode the reply must go through genai.Client(vertexai=True) (ADC),
    # not the Developer API — mirrors the analysis path so the two can't drift.
    from arhiteq_api.api import chats

    chat = await _create_chat(client)
    monkeypatch.setattr(
        chats,
        "get_settings",
        lambda: Settings(google_genai_use_vertexai=True, analysis_model="gemini-x"),
    )
    fake = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content=AsyncMock(return_value=SimpleNamespace(text="Hi from Vertex!"))
            )
        )
    )
    with patch("google.genai.Client", return_value=fake) as mk:
        resp = await client.post(
            "/create-chat-completion",
            headers=AUTH_HEADERS,
            json={"chat_id": chat["chat_id"], "content": "hello"},
        )
    assert resp.status_code == 201
    assert resp.json()["messages"][0]["content"] == "Hi from Vertex!"
    mk.assert_called_once_with(vertexai=True)


async def test_chat_completion_flags_fallback_without_creds(client):
    # Test env has no Gemini creds, so the reply is the canned placeholder and
    # must be flagged is_fallback so the dashboard can warn instead of passing
    # it off as a real answer.
    chat = await _create_chat(client)
    resp = await client.post(
        "/create-chat-completion",
        headers=AUTH_HEADERS,
        json={"chat_id": chat["chat_id"], "content": "hi"},
    )
    assert resp.status_code == 201
    assert resp.json().get("is_fallback") is True


async def _set_llm_model(model: str) -> None:
    from arhiteq_api.db import session_factory
    from arhiteq_api.models import RetellLLM
    from tests.conftest import LLM_ID

    async with session_factory()() as s:
        llm = await s.get(RetellLLM, LLM_ID)
        llm.model = model
        await s.commit()


def _fake_genai(text: str):
    fake = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content=AsyncMock(return_value=SimpleNamespace(text=text))
            )
        )
    )
    return fake, patch("google.genai.Client", return_value=fake)


async def test_chat_completion_uses_agent_text_model_and_no_fallback_flag(client, monkeypatch):
    from arhiteq_api.api import chats

    await _set_llm_model("gemini-3.5-flash")
    chat = await _create_chat(client)
    monkeypatch.setattr(
        chats,
        "get_settings",
        lambda: Settings(google_genai_use_vertexai=True, analysis_model="gemini-analysis"),
    )
    fake, patcher = _fake_genai("real reply")
    with patcher:
        resp = await client.post(
            "/create-chat-completion",
            headers=AUTH_HEADERS,
            json={"chat_id": chat["chat_id"], "content": "hi"},
        )
    body = resp.json()
    assert body["messages"][0]["content"] == "real reply"
    assert "is_fallback" not in body
    # The agent's own text model is tested, not the platform analysis model.
    assert fake.aio.models.generate_content.call_args.kwargs["model"] == "gemini-3.5-flash"


async def test_chat_completion_live_model_falls_back_to_analysis_model(client, monkeypatch):
    from arhiteq_api.api import chats

    # Live (native-audio) models can't serve text generate_content.
    await _set_llm_model("gemini-live-2.5-flash-native-audio")
    chat = await _create_chat(client)
    monkeypatch.setattr(
        chats,
        "get_settings",
        lambda: Settings(google_genai_use_vertexai=True, analysis_model="gemini-analysis"),
    )
    fake, patcher = _fake_genai("ok")
    with patcher:
        await client.post(
            "/create-chat-completion",
            headers=AUTH_HEADERS,
            json={"chat_id": chat["chat_id"], "content": "hi"},
        )
    assert fake.aio.models.generate_content.call_args.kwargs["model"] == "gemini-analysis"


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
    assert agent_msg["content"]  # canned reply without ARHITEQ_GOOGLE_API_KEY

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
