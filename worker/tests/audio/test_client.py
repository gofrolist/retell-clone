"""Starting a call, tested against a transport that never leaves.

The failures worth catching here all look like success: a call created against
the wrong agent, a response missing the token, variables sent under a name the
platform ignores. Each of those produces a run, a recording, and a report.
"""

from __future__ import annotations

import json

import httpx
import pytest

from audio.client import ControlPlaneError, create_web_call, get_call, transcript_lines

CALL = {
    "call_id": "call_abc",
    "access_token": "jwt-goes-here",
    "livekit_server_url": "ws://127.0.0.1:7880",
    "agent_id": "agent_1",
}


def recorder(handler):
    sent: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(capture)), sent


def created(_request):
    return httpx.Response(201, json=CALL)


def test_the_variables_travel_under_the_name_the_contract_froze():
    # Sent under any other key they are ignored, the prompt renders with its
    # placeholder defaults, and the call runs — greeting a caller the case
    # never described.
    client, sent = recorder(created)
    create_web_call(
        "http://api", "key", "agent_1", variables={"first_name": "Margaret"}, client=client
    )
    body = json.loads(sent[0].read())
    assert body["retell_llm_dynamic_variables"] == {"first_name": "Margaret"}
    assert body["agent_id"] == "agent_1"


def test_the_key_travels_as_a_bearer_token():
    client, sent = recorder(created)
    create_web_call("http://api", "key_123", "agent_1", client=client)
    assert sent[0].headers["Authorization"] == "Bearer key_123"


def test_a_trailing_slash_on_the_base_does_not_double_up():
    client, sent = recorder(created)
    create_web_call("http://api/", "key", "agent_1", client=client)
    assert str(sent[0].url) == "http://api/v2/create-web-call"


def test_empty_variables_are_left_out_rather_than_sent_as_an_empty_object():
    client, sent = recorder(created)
    create_web_call("http://api", "key", "agent_1", client=client)
    assert "retell_llm_dynamic_variables" not in json.loads(sent[0].read())


def test_a_pinned_agent_version_is_passed_through():
    # Layer 1 grades `dist/`; this layer has to be able to grade the same
    # version rather than whichever draft happens to be published.
    client, sent = recorder(created)
    create_web_call("http://api", "key", "agent_1", agent_version=7, client=client)
    assert json.loads(sent[0].read())["agent_version"] == 7


def test_a_refusal_carries_the_body_that_explains_it():
    client, _ = recorder(lambda _r: httpx.Response(404, text="Agent not found"))
    with pytest.raises(ControlPlaneError, match="Agent not found"):
        create_web_call("http://api", "key", "agent_nope", client=client)


def test_a_response_without_a_token_fails_here_not_at_the_room_join():
    # Without this the caller bot fails forty lines later, inside LiveKit, as
    # an authentication error that says nothing about the call never having
    # been given a token.
    client, _ = recorder(lambda _r: httpx.Response(201, json={"call_id": "call_abc"}))
    with pytest.raises(ControlPlaneError, match="access_token"):
        create_web_call("http://api", "key", "agent_1", client=client)


def test_a_response_without_a_server_url_is_caught_too():
    client, _ = recorder(lambda _r: httpx.Response(201, json={"call_id": "c", "access_token": "t"}))
    with pytest.raises(ControlPlaneError, match="livekit_server_url"):
        create_web_call("http://api", "key", "agent_1", client=client)


def test_fetching_a_call_asks_for_the_one_that_was_run():
    client, sent = recorder(lambda _r: httpx.Response(200, json=CALL))
    assert get_call("http://api", "key", "call_abc", client=client)["call_id"] == "call_abc"
    assert str(sent[0].url).endswith("/v2/get-call/call_abc")


def test_the_platform_transcript_comes_back_as_roles_and_words():
    call = {
        "transcript_object": [
            {"role": "agent", "content": "Good morning, Margaret."},
            {"role": "user", "content": "Morning."},
            {"role": "agent", "content": "", "tool_calls": [{"name": "log_mood"}]},
        ]
    }
    assert transcript_lines(call) == [
        ("agent", "Good morning, Margaret."),
        ("user", "Morning."),
    ]


def test_a_call_with_no_transcript_yet_is_no_lines_not_a_crash():
    assert transcript_lines({}) == []
    assert transcript_lines({"transcript_object": None}) == []
