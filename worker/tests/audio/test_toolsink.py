"""The sink, and the refusals that keep a case on this machine.

The tests that matter most here are the ones about `require_sunk` and about
`rewrite` refusing a non-loopback API. Everything else in this layer costs a
call when it is wrong; those two cost somebody a text message at seven in the
morning, or a production agent quietly calling a laptop.
"""

from __future__ import annotations

import json

import httpx
import pytest

from audio.toolsink import (
    DEFAULT_PORT,
    DEFAULT_RESULT,
    SinkError,
    ToolSink,
    as_dialled,
    base_url,
    is_sunk,
    live_tools,
    reachable,
    require_sunk,
    rewrite,
    sink_tools,
    tool_url,
)

SINK = base_url(DEFAULT_PORT)

REAL = "https://mrnlotdwthdqcaicwyql.supabase.co/functions/v1/log-mood"


def custom(name, url=REAL):
    return {"name": name, "type": "custom", "url": url, "method": "POST"}


def built_in(name, type_):
    return {"name": name, "type": type_}


# --- answering -----------------------------------------------------------


def test_a_mocked_tool_gets_its_canned_result():
    sink = ToolSink(results={"log_mood": '{"ok": true, "streak": 3}'})
    assert sink.record("log_mood", {"mood_score": 4}) == '{"ok": true, "streak": 3}'
    assert sink.invocations[0].arguments == {"mood_score": 4}
    assert sink.invocations[0].mocked is True


def test_an_unmocked_tool_still_gets_an_answer_and_is_named():
    # Failing the call would grade the case's imagination rather than the
    # prompt. Saying nothing would hide that the payload was invented here.
    sink = ToolSink()
    assert sink.record("save_medication", {}) == DEFAULT_RESULT
    assert sink.unmocked == ["save_medication"]


def test_unmocked_names_each_tool_once_in_call_order():
    sink = ToolSink(results={"log_mood": "{}"})
    sink.record("recall_lead_context", {})
    sink.record("log_mood", {})
    sink.record("save_medication", {})
    sink.record("recall_lead_context", {})
    assert sink.unmocked == ["recall_lead_context", "save_medication"]


# --- the socket ----------------------------------------------------------


def test_the_server_answers_a_rewritten_url():
    sink = ToolSink(results={"log_mood": '{"ok": true}'}, port=8123)
    sink.start()
    try:
        response = httpx.post(tool_url("log_mood", port=8123), json={"mood_score": 4}, timeout=5)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
    finally:
        sink.stop()
    assert [i.name for i in sink.invocations] == ["log_mood"]
    assert sink.invocations[0].arguments == {"mood_score": 4}


def test_a_body_that_is_not_json_is_still_a_call_that_happened():
    sink = ToolSink(port=8124)
    sink.start()
    try:
        response = httpx.post(
            tool_url("log_mood", port=8124),
            content=b"not json",
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert response.status_code == 200
    finally:
        sink.stop()
    assert [i.name for i in sink.invocations] == ["log_mood"]


def test_stop_is_safe_to_call_when_it_never_started():
    ToolSink().stop()


# --- rewriting -----------------------------------------------------------


def test_a_custom_tool_is_repointed_and_everything_else_is_left_alone():
    tool = {**custom("log_mood"), "description": "Logs the mood.", "parameters": {"a": 1}}
    rewritten, changed = sink_tools([tool], sink_base=SINK)
    assert changed == ["log_mood"]
    assert rewritten[0]["url"] == f"{SINK}/tool/log_mood"
    # The agent under test has to be the production agent in every respect but
    # where its calls land.
    assert rewritten[0]["description"] == "Logs the mood."
    assert rewritten[0]["parameters"] == {"a": 1}


def test_built_in_tools_are_left_alone():
    tools = [built_in("end_call", "end_call"), built_in("to_pharmacy", "agent_swap")]
    rewritten, changed = sink_tools(tools, sink_base=SINK)
    assert changed == []
    assert rewritten == tools


def test_rewriting_twice_changes_nothing_the_second_time():
    once, _ = sink_tools([custom("log_mood")], sink_base=SINK)
    twice, changed = sink_tools(once, sink_base=SINK)
    assert changed == []
    assert twice == once


def test_live_tools_lists_only_what_would_reach_the_world():
    tools = [custom("log_mood"), custom("send_family_sms"), built_in("end_call", "end_call")]
    sunk, _ = sink_tools([tools[0]], sink_base=SINK)
    assert live_tools(sunk + tools[1:], sink_base=SINK) == [("send_family_sms", REAL)]


def test_is_sunk_is_not_fooled_by_a_url_that_merely_starts_similarly():
    assert is_sunk(f"{SINK}/tool/log_mood", sink_base=SINK)
    assert not is_sunk("https://evil.example/tool/log_mood", sink_base=SINK)
    assert not is_sunk(None, sink_base=SINK)


# --- the refusals --------------------------------------------------------


def test_require_sunk_refuses_an_agent_that_would_write_real_data():
    with pytest.raises(SinkError, match="real endpoints"):
        require_sunk([custom("send_family_sms")], agent="clara-checkin", sink_base=SINK)


def test_require_sunk_names_the_tools_and_where_they_point():
    with pytest.raises(SinkError, match="send_family_sms"):
        require_sunk([custom("send_family_sms")], agent="clara-checkin", sink_base=SINK)


def test_require_sunk_passes_an_agent_with_only_built_ins():
    # The smoke agent has no custom tools at all, and must not need rewriting
    # before the plumbing case can run.
    require_sunk([built_in("end_call", "end_call")], agent="audio-smoke", sink_base=SINK)


def test_require_sunk_passes_once_everything_is_rewritten():
    sunk, _ = sink_tools([custom("log_mood"), custom("send_family_sms")], sink_base=SINK)
    require_sunk(sunk, agent="clara-checkin", sink_base=SINK)


@pytest.mark.parametrize(
    "api_base",
    ["https://api.arhiteq.com", "http://10.0.0.5:8080", "https://dashboard.arhiteq.com"],
)
def test_rewrite_refuses_anything_that_is_not_a_local_stack(api_base):
    # Run against production and every real Clara tool call starts arriving at
    # a laptop that is not listening — and does not fail loudly, because the
    # agent talks its way past a tool error.
    with pytest.raises(SinkError, match="only ever meant for a local stack"):
        rewrite(
            api_base=api_base, api_key="key", agent_ids={"clara-checkin": "agent_1"}, sink_base=SINK
        )


@pytest.mark.parametrize("api_base", ["http://127.0.0.1:8080", "http://localhost:8080"])
def test_rewrite_accepts_a_local_stack(api_base):
    transport = httpx.MockTransport(_local_api)
    with httpx.Client(transport=transport) as client:
        changes = rewrite(
            api_base=api_base,
            api_key="key",
            agent_ids={"clara-checkin": "agent_1"},
            sink_base=SINK,
            client=client,
        )
    assert changes["clara-checkin"].repointed == ["log_mood"]


def test_rewrite_says_so_when_an_agent_keeps_its_tools_somewhere_else():
    def flow_agent(request):
        return httpx.Response(
            200,
            json={"response_engine": {"type": "conversation-flow", "conversation_flow_id": "cf_1"}},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(flow_agent)) as client,
        pytest.raises(SinkError, match="no llm_id"),
    ):
        rewrite(
            api_base="http://127.0.0.1:8080",
            api_key="key",
            agent_ids={"clara-checkin": "agent_1"},
            sink_base=SINK,
            client=client,
        )


# --- following the swap graph --------------------------------------------


def swap(name, agent_id):
    return {"name": name, "type": "agent_swap", "agent_id": agent_id}


def _clara(request: httpx.Request) -> httpx.Response:
    """Two agents that hand the call back and forth, the way Clara's five do."""
    agents = {
        "agent_checkin": (
            "llm_checkin",
            [custom("log_mood"), swap("to_pharmacy", "agent_pharmacy")],
        ),
        "agent_pharmacy": (
            "llm_pharmacy",
            [custom("send_coupon_sms"), swap("hand_back_to_checkin", "agent_checkin")],
        ),
    }
    path = request.url.path
    for agent_id, (llm_id, tools) in agents.items():
        if path == f"/get-agent/{agent_id}":
            return httpx.Response(200, json={"response_engine": {"llm_id": llm_id}, "version": 1})
        if path == f"/get-agent-version/{agent_id}/1":
            return httpx.Response(200, json={"response_engine_config": {"general_tools": tools}})
        if path == f"/get-retell-llm/{llm_id}":
            return httpx.Response(200, json={"general_tools": tools})
        if path == f"/update-retell-llm/{llm_id}":
            return httpx.Response(200, json={"llm_id": llm_id})
        if path == f"/publish-agent/{agent_id}":
            return httpx.Response(200, json={"agent_id": agent_id})
    return httpx.Response(404)


def test_reachable_follows_a_swap_to_the_specialist():
    # A call that starts at the check-in and asks a price finishes inside the
    # pharmacy, running the pharmacy's tools. Checking only where the call
    # STARTS would clear a run that texts a real coupon to a real phone.
    with httpx.Client(transport=httpx.MockTransport(_clara)) as client:
        configs = reachable("http://127.0.0.1:8080", "key", "agent_checkin", client=client)
    assert {c.agent_id for c in configs} == {"agent_checkin", "agent_pharmacy"}


def test_reachable_terminates_on_a_ring():
    # Every specialist carries `hand_back_to_checkin`, so the graph is a ring
    # and a naive walk never returns.
    with httpx.Client(transport=httpx.MockTransport(_clara)) as client:
        configs = reachable("http://127.0.0.1:8080", "key", "agent_pharmacy", client=client)
    assert len(configs) == 2


def test_rewrite_reaches_an_agent_nobody_listed():
    # The map names one agent; the specialist behind the swap is rewritten too,
    # because the whole reason to follow the graph is that it holds agents
    # nobody remembered to list.
    with httpx.Client(transport=httpx.MockTransport(_clara)) as client:
        changes = rewrite(
            api_base="http://127.0.0.1:8080",
            api_key="key",
            agent_ids={"clara-checkin": "agent_checkin"},
            sink_base=SINK,
            client=client,
        )
    assert {k: v.repointed for k, v in changes.items()} == {
        "clara-checkin": ["log_mood"],
        "agent_pharmacy": ["send_coupon_sms"],
    }


# --- published snapshot vs live rows -------------------------------------
#
# The hole these cover is not hypothetical. The first Clara audio call ran with
# every live row rewritten and reported "safe", then POSTed a medication log to
# production Supabase, because `PATCH /update-retell-llm` opens a DRAFT and a
# call runs `latest_published`.


def _versioned(live_tools_list, published_tools_list):
    """An API whose draft and published snapshot disagree, which is the norm."""

    def handler(request: httpx.Request) -> httpx.Response:
        path, query = request.url.path, request.url.params
        if path == "/get-agent/agent_1":
            # The endpoint defaults to `latest` — the draft — and answers
            # `latest_published` with the published version number.
            published = str(query.get("version")) == "latest_published"
            return httpx.Response(
                200,
                json={"response_engine": {"llm_id": "llm_1"}, "version": 2 if published else 3},
            )
        if path == "/get-agent-version/agent_1/2":
            return httpx.Response(
                200, json={"response_engine_config": {"general_tools": published_tools_list}}
            )
        if path == "/get-agent-version/agent_1/3":
            return httpx.Response(
                200, json={"response_engine_config": {"general_tools": live_tools_list}}
            )
        if path == "/get-retell-llm/llm_1":
            return httpx.Response(200, json={"general_tools": live_tools_list})
        if path in ("/update-retell-llm/llm_1", "/publish-agent/agent_1"):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_as_dialled_reads_the_published_snapshot_not_the_rewritten_draft():
    sunk, _ = sink_tools([custom("log_mood")], sink_base=SINK)
    transport = _versioned(live_tools_list=sunk, published_tools_list=[custom("log_mood")])
    with httpx.Client(transport=transport) as client:
        configs = as_dialled("http://127.0.0.1:8080", "key", "agent_1", client=client)
    # Live rows say sunk; the call would still dial Supabase. The check has to
    # see the second one.
    with pytest.raises(SinkError, match="real endpoints"):
        require_sunk(configs[0].tools, agent="clara-checkin", sink_base=SINK)


def test_as_dialled_passes_once_the_rewrite_has_been_published():
    sunk, _ = sink_tools([custom("log_mood")], sink_base=SINK)
    transport = _versioned(live_tools_list=sunk, published_tools_list=sunk)
    with httpx.Client(transport=transport) as client:
        configs = as_dialled("http://127.0.0.1:8080", "key", "agent_1", client=client)
    require_sunk(configs[0].tools, agent="clara-checkin", sink_base=SINK)


def test_as_dialled_honours_a_pinned_version():
    sunk, _ = sink_tools([custom("log_mood")], sink_base=SINK)
    transport = _versioned(live_tools_list=sunk, published_tools_list=[custom("log_mood")])
    with httpx.Client(transport=transport) as client:
        # A case pinning the draft is dialling the draft, and that is what has
        # to be checked.
        configs = as_dialled("http://127.0.0.1:8080", "key", "agent_1", version=3, client=client)
    require_sunk(configs[0].tools, agent="clara-checkin", sink_base=SINK)


def test_rewrite_publishes_so_calls_see_it():
    published = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/publish-agent/agent_1":
            published.append(request.url.path)
        return _versioned([custom("log_mood")], [custom("log_mood")]).handler(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        rewrite(
            api_base="http://127.0.0.1:8080",
            api_key="key",
            agent_ids={"clara-checkin": "agent_1"},
            sink_base=SINK,
            client=client,
        )
    # A draft nobody publishes is a config change no call ever sees.
    assert published == ["/publish-agent/agent_1"]


def test_rewrite_does_not_publish_when_nothing_changed():
    sunk, _ = sink_tools([custom("log_mood")], sink_base=SINK)
    published = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/publish-agent/agent_1":
            published.append(request.url.path)
        return _versioned(sunk, sunk).handler(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        rewrite(
            api_base="http://127.0.0.1:8080",
            api_key="key",
            agent_ids={"clara-checkin": "agent_1"},
            sink_base=SINK,
            client=client,
        )
    # Re-running the setup step must not churn a version every time.
    assert published == []


def _local_api(request: httpx.Request) -> httpx.Response:
    """A local control plane holding one agent with one custom tool."""
    if request.url.path == "/get-agent/agent_1":
        return httpx.Response(
            200,
            json={"response_engine": {"type": "retell-llm", "llm_id": "llm_1"}, "version": 1},
        )
    if request.url.path == "/get-agent-version/agent_1/1":
        return httpx.Response(
            200, json={"response_engine_config": {"general_tools": [custom("log_mood")]}}
        )
    if request.url.path == "/get-retell-llm/llm_1":
        return httpx.Response(200, json={"general_tools": [custom("log_mood")]})
    if request.url.path == "/update-retell-llm/llm_1":
        sent = json.loads(request.content)
        assert sent["general_tools"][0]["url"] == f"{SINK}/tool/log_mood"
        return httpx.Response(200, json={"llm_id": "llm_1"})
    if request.url.path == "/publish-agent/agent_1":
        return httpx.Response(200, json={"agent_id": "agent_1"})
    return httpx.Response(404)
