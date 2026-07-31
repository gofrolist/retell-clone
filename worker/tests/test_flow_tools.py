"""Flow tool wrapping. Requires the livekit stack — skipped in the dev-only env.

`arhiteq_worker.flow` holds the decision layer (`transition_tool_schema`,
`FlowGraph`, ...) and stays livekit-free so the worker's dev-only CI can
import it. The three constructors under test here are the thin part that
*does* import livekit -- lazily, inside each factory function, exactly like
every constructor in `tools.py` -- to turn a node's behaviour into an actual
`function_tool`. livekit-agents calls `typing.get_type_hints()` on a tool's
handler at *execution* time to find its `RunContext` parameter
(`test_tool_annotations.py` exists because a bad/stringized annotation there
only explodes mid-call), so every tool built here is checked the same way.
"""

import asyncio
import json
import logging
import typing

import httpx
import pytest

pytest.importorskip("livekit.agents")

from livekit.agents import RunContext

from arhiteq_worker import flow as flow_module
from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.flow import (
    FlowGraph,
    make_extract_node_tool,
    make_flow_kb_lookup_tool,
    make_function_node_tool,
    make_transition_tool,
    transition_tool_schema,
)
from arhiteq_worker.state import CallState


def _run(coro):
    return asyncio.run(coro)


def _graph(flow_dict: dict) -> FlowGraph:
    return FlowGraph.from_config(ConversationFlowConfig.from_dict(flow_dict))


def _hints(tool) -> dict:
    # Mirrors test_tool_annotations.py exactly: livekit wraps the handler in
    # a RawFunctionTool whose own __annotations__ proxy the original
    # function's, so get_type_hints on the tool itself is what livekit-agents
    # does at call time.
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    return typing.get_type_hints(fnc)


class _FakeSession:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str, add_to_chat_ctx: bool = True) -> None:
        self.said.append(text)


class _FakeContext:
    def __init__(self) -> None:
        self.session = _FakeSession()


class _FakeKnowledge:
    def __init__(self, results: dict | None = None) -> None:
        self.calls: list[dict] = []
        self._results = results if results is not None else {"results": []}

    async def search_knowledge_base(
        self,
        call_id: str,
        query: str,
        *,
        knowledge_base_ids=None,
        category=None,
        top_k=None,
    ) -> dict:
        self.calls.append(
            {
                "call_id": call_id,
                "query": query,
                "knowledge_base_ids": knowledge_base_ids,
                "category": category,
                "top_k": top_k,
            }
        )
        return self._results


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _prompt_condition(text: str) -> dict:
    return {"type": "prompt", "prompt": text}


# ---------------------------------------------------------------------------
# make_transition_tool
# ---------------------------------------------------------------------------


def test_transition_tool_resolves_type_hints_and_has_the_runtime_expected_name() -> None:
    node = {"id": "n1", "name": "Ask"}
    edges = [
        {
            "id": "p1",
            "transition_condition": _prompt_condition("Yes"),
            "destination_node_id": "n2",
        },
        {
            "id": "p2",
            "transition_condition": _prompt_condition("No"),
            "destination_node_id": "n3",
        },
    ]
    schema = transition_tool_schema(node, edges)
    assert schema is not None

    state = CallState(call_id="call_1")
    calls: list[str] = []

    async def on_transition(edge_id: str) -> None:
        calls.append(edge_id)

    tool = make_transition_tool(schema, on_transition, state=state)

    hints = _hints(tool)
    assert hints.get("context") is RunContext
    assert tool.info.name == "transition_to"
    # The enum built by transition_tool_schema must survive into the actual
    # parameter schema the model sees.
    assert schema["enum"] == ["p1", "p2"]


def test_transition_tool_handler_calls_on_transition_with_the_chosen_edge_id() -> None:
    node = {"id": "n1"}
    edges = [
        {
            "id": "p1",
            "transition_condition": _prompt_condition("Yes"),
            "destination_node_id": "n2",
        }
    ]
    schema = transition_tool_schema(node, edges)
    state = CallState(call_id="call_1")
    calls: list[str] = []

    async def on_transition(edge_id: str) -> None:
        calls.append(edge_id)

    tool = make_transition_tool(schema, on_transition, state=state)
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    result = _run(fnc({"transition_to": "p1"}, None))

    assert calls == ["p1"]
    assert result is not None
    # The invocation is recorded on CallState like every other tool.
    kinds = [item["role"] for item in state.items]
    assert "tool_call_invocation" in kinds
    assert "tool_call_result" in kinds


def test_transition_tool_records_a_failed_transition_without_raising() -> None:
    schema = transition_tool_schema({"id": "n1"}, [{"id": "p1", "destination_node_id": "n2"}])
    state = CallState(call_id="call_1")

    async def on_transition(edge_id: str) -> None:
        raise RuntimeError("boom")

    tool = make_transition_tool(schema, on_transition, state=state)
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    # Must not raise: a bug in the runtime must not kill the model's turn.
    result = _run(fnc({"transition_to": "p1"}, None))
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# make_function_node_tool
# ---------------------------------------------------------------------------


def _function_node(**extra) -> dict:
    node = {
        "id": "fn1",
        "type": "function",
        "tool_id": "tool-1",
        "edges": [
            {
                "id": "success",
                "transition_condition": _prompt_condition("Succeeded"),
                "destination_node_id": "n_success",
            }
        ],
        "else_edge": {
            "id": "failure",
            "transition_condition": _prompt_condition("Else"),
            "destination_node_id": "n_failure",
        },
    }
    node.update(extra)
    return node


_FLOW_TOOLS = [
    {
        "tool_id": "tool-1",
        "type": "custom",
        "name": "get_member",
        "url": "https://template-agents-api.example.com/get_member",
        "method": "POST",
        "parameters": {
            "type": "object",
            "properties": {"call_first_name": {"type": "string"}},
            "required": ["call_first_name"],
        },
        "response_variables": {"member_id": "memberId"},
    }
]


def test_function_node_tool_resolves_type_hints_and_schema_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"memberId": "M-1"})

    variables: dict = {}
    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    tool = make_function_node_tool(
        _function_node(),
        _FLOW_TOOLS,
        http=_client(handler),
        function_secret="s",
        variables=variables,
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    assert tool is not None
    hints = _hints(tool)
    assert hints.get("context") is RunContext
    assert tool.info.name == "get_member"


def test_function_node_tool_success_merges_response_variables_and_advances() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"memberId": "M-1"})

    variables: dict = {}
    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    node = _function_node()
    tool = make_function_node_tool(
        node,
        _FLOW_TOOLS,
        http=_client(handler),
        function_secret="s",
        variables=variables,
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    context = _FakeContext()
    _run(fnc({"call_first_name": "Jo"}, context))

    assert variables == {"member_id": "M-1"}
    assert calls == [node["edges"][0]]


def test_function_node_tool_failure_advances_the_else_edge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    variables: dict = {}
    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    node = _function_node()
    tool = make_function_node_tool(
        node,
        _FLOW_TOOLS,
        http=_client(handler),
        function_secret="s",
        variables=variables,
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    result = _run(fnc({"call_first_name": "Jo"}, _FakeContext()))

    assert calls == [node["else_edge"]]
    assert json.loads(result).get("error")


# ---------------------------------------------------------------------------
# make_function_node_tool: success routing with more than one prompt edge
# ---------------------------------------------------------------------------
#
# Real-fixture regression (backend/tests/fixtures/retell_flows/
# prior_auth_hotline.json): node "node-1773865257897" ("Get Member") carries
# TWO prompt edges ("Successfully get the member information" / "get_member
# has failed twice to get information") plus an else_edge, while node
# "node-1773865358553" ("Get Pa Cases") carries exactly ONE. Only a hard HTTP
# failure may auto-route to else_edge; a successful call with more than one
# prompt edge must leave the choice to the model instead of blindly
# following edges[0] — the model just saw the tool result and is the only
# thing that can tell "found the member" apart from "failed twice".


def test_function_node_tool_two_prompt_edges_does_not_auto_advance_on_success(
    prior_auth_flow,
) -> None:
    graph = _graph(prior_auth_flow)
    node = graph.node("node-1773865257897")  # "Get Member": 2 prompt edges
    assert len(node["edges"]) == 2

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"memberId": "M-1"})

    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    tool = make_function_node_tool(
        node,
        prior_auth_flow["tools"],
        http=_client(handler),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    assert tool is not None
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    result = _run(
        fnc(
            {"call_first_name": "Jo", "call_last_name": "Doe", "call_dob": "2000-01-01"},
            _FakeContext(),
        )
    )

    # Ambiguous success: the model must choose via its own transition_to
    # tool, not have this handler pick edges[0] for it.
    assert calls == []
    assert json.loads(result) == {"memberId": "M-1"}


def test_function_node_tool_single_prompt_edge_auto_advances_on_success(
    prior_auth_flow,
) -> None:
    graph = _graph(prior_auth_flow)
    node = graph.node("node-1773865358553")  # "Get Pa Cases": 1 prompt edge
    assert len(node["edges"]) == 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    tool = make_function_node_tool(
        node,
        prior_auth_flow["tools"],
        http=_client(handler),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    assert tool is not None
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    _run(fnc({"member_id": "M-1"}, _FakeContext()))

    # Unambiguous: the single prompt edge is followed automatically.
    assert calls == [node["edges"][0]]


def test_function_node_tool_two_prompt_edges_error_advances_the_else_edge(
    prior_auth_flow,
) -> None:
    graph = _graph(prior_auth_flow)
    # The real "Get Member" else_edge is dangling (no destination_node_id —
    # see test_flow_transitions.test_fallback_edge_against_the_real_fixture_
    # dangling_cases); give it one here so this test can observe the
    # fallback actually being followed. The ambiguity under test lives in
    # `edges[]`, not `else_edge`.
    node = dict(graph.node("node-1773865257897"))
    node["else_edge"] = {**node["else_edge"], "destination_node_id": "n_failure"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    tool = make_function_node_tool(
        node,
        prior_auth_flow["tools"],
        http=_client(handler),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    assert tool is not None
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    _run(
        fnc(
            {"call_first_name": "Jo", "call_last_name": "Doe", "call_dob": "2000-01-01"},
            _FakeContext(),
        )
    )

    # Hard failure always routes to else_edge, regardless of how many
    # prompt edges the node carries.
    assert calls == [node["else_edge"]]


def test_function_node_tool_single_prompt_edge_error_advances_the_else_edge(
    prior_auth_flow,
) -> None:
    graph = _graph(prior_auth_flow)
    node = graph.node("node-1773865358553")  # "Get Pa Cases": real else_edge has a destination
    assert node["else_edge"].get("destination_node_id")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    tool = make_function_node_tool(
        node,
        prior_auth_flow["tools"],
        http=_client(handler),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    assert tool is not None
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    _run(fnc({"member_id": "M-1"}, _FakeContext()))

    assert calls == [node["else_edge"]]


def test_function_node_tool_speaks_a_filler_when_speak_during_execution() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    state = CallState(call_id="call_1")

    async def on_transition(edge: dict) -> None:
        return None

    node = _function_node(speak_during_execution=True)
    tool = make_function_node_tool(
        node,
        _FLOW_TOOLS,
        http=_client(handler),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    context = _FakeContext()
    _run(fnc({"call_first_name": "Jo"}, context))

    assert context.session.said


def test_function_node_tool_wait_for_result_false_advances_without_waiting() -> None:
    """The handler must return before the backgrounded HTTP call completes.

    `release` is only ever set *after* the handler call has already
    returned, and `slow_request` only marks `request_completed` after
    `release` unblocks it. So if the handler returned, `request_completed`
    provably cannot be set yet -- that ordering is a hard guarantee, not
    "scheduling is not guaranteed here". Every wait carries a timeout: if a
    future change turns `asyncio.create_task(run())` back into `await
    run()`, the handler call itself would deadlock (it would be the one
    awaiting the still-unset `release`), and `asyncio.wait_for` turns that
    deadlock into a fast, clear failure instead of a hang.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    request_completed = asyncio.Event()

    async def slow_request(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        request_completed.set()
        return httpx.Response(200, json={"memberId": "M-1"})

    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    node = _function_node(wait_for_result=False)
    tool = make_function_node_tool(
        node,
        _FLOW_TOOLS,
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow_request)),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)

    async def scenario() -> None:
        result = await asyncio.wait_for(fnc({"call_first_name": "Jo"}, _FakeContext()), timeout=5.0)
        # Proof the handler did not wait: the request cannot have completed
        # yet, since nothing has released it.
        assert result is not None
        assert not request_completed.is_set()

        # The background task really was scheduled (not silently dropped):
        # it reaches the transport and blocks there until released.
        await asyncio.wait_for(started.wait(), timeout=5.0)
        assert not request_completed.is_set()

        release.set()
        await asyncio.wait_for(request_completed.wait(), timeout=5.0)

    _run(scenario())
    assert calls == [node["edges"][0]]


def test_function_node_tool_wait_for_result_false_retains_the_background_task() -> None:
    """The fire-and-forget request must be held by a strong reference.

    The event loop keeps only a *weak* reference to a task, so
    ``asyncio.create_task(run())`` with the result thrown away can be
    garbage-collected before the request ever reaches the transport —
    silently, and with any exception it raises never logged. `main.py`
    documents this exact hazard and holds every task it spawns in
    `_background_tasks`; the test above cannot catch a regression here
    because it holds a live reference of its own through the transport's
    events. This one asserts the module keeps the reference itself, and drops
    it again once the task completes.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_request(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json={"memberId": "M-1"})

    async def on_transition(edge: dict) -> None:
        return None

    tool = make_function_node_tool(
        _function_node(wait_for_result=False),
        _FLOW_TOOLS,
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow_request)),
        function_secret="s",
        variables={},
        call_info=None,
        state=CallState(call_id="call_1"),
        on_transition=on_transition,
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)

    async def scenario() -> None:
        await asyncio.wait_for(fnc({"call_first_name": "Jo"}, _FakeContext()), timeout=5.0)
        await asyncio.wait_for(started.wait(), timeout=5.0)
        in_flight = [task for task in flow_module._background_tasks if not task.done()]
        assert in_flight, "the backgrounded request is not retained anywhere"

        release.set()
        await asyncio.gather(*in_flight)
        # Completed tasks are discarded again: the set is not a leak.
        assert not (set(in_flight) & flow_module._background_tasks)

    _run(scenario())


def test_a_detached_flow_task_that_raises_is_logged(caplog) -> None:
    """...and its failure is surfaced, not swallowed with the reference."""

    async def boom() -> None:
        raise RuntimeError("the request never made it")

    async def scenario() -> None:
        task = flow_module._spawn_detached(boom())
        with pytest.raises(RuntimeError):
            await task

    with caplog.at_level(logging.ERROR, logger="arhiteq-worker.flow"):
        _run(scenario())

    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_function_node_tool_returns_none_for_an_unresolved_tool_id() -> None:
    state = CallState(call_id="call_1")

    async def on_transition(edge: dict) -> None:
        return None

    tool = make_function_node_tool(
        _function_node(tool_id="does-not-exist"),
        _FLOW_TOOLS,
        http=httpx.AsyncClient(),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    assert tool is None


def test_function_node_tool_speak_after_execution_false_raises_stop_response() -> None:
    """`speak_after_execution: false` -> the model must not react to the result.

    Mirrors `tools._make_http_tool`'s documented behaviour: raising
    `livekit.agents.llm.StopResponse` from the handler is livekit-agents' own
    signal for "don't have the LLM respond to this tool result". The
    transition must still happen first -- only whether the model *speaks*
    about the result is suppressed, not the graph walk.
    """
    from livekit.agents.llm import StopResponse

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"memberId": "M-1"})

    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    node = _function_node()
    flow_tools = [{**_FLOW_TOOLS[0], "speak_after_execution": False}]
    tool = make_function_node_tool(
        node,
        flow_tools,
        http=_client(handler),
        function_secret="s",
        variables={},
        call_info=None,
        state=state,
        on_transition=on_transition,
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)

    with pytest.raises(StopResponse):
        _run(fnc({"call_first_name": "Jo"}, _FakeContext()))

    # The transition still happened before the StopResponse was raised.
    assert calls == [node["edges"][0]]


# ---------------------------------------------------------------------------
# make_extract_node_tool
# ---------------------------------------------------------------------------


def _extract_node(**extra) -> dict:
    node = {
        "id": "ex1",
        "type": "extract_dynamic_variables",
        "variables": [
            {"name": "plan", "type": "string", "description": "insurance plan"},
            {
                "name": "confirmed",
                "type": "boolean",
                "description": "caller confirmed",
            },
        ],
        "edges": [
            {
                "id": "extracted",
                "transition_condition": _prompt_condition("Extracted"),
                "destination_node_id": "n_extracted",
            }
        ],
        "else_edge": {
            "id": "empty",
            "transition_condition": _prompt_condition("Else"),
            "destination_node_id": "n_empty",
        },
    }
    node.update(extra)
    return node


def test_extract_node_tool_resolves_type_hints_and_schema_name() -> None:
    state = CallState(call_id="call_1")

    async def on_transition(edge: dict) -> None:
        return None

    tool = make_extract_node_tool(
        _extract_node(), variables={}, state=state, on_transition=on_transition
    )
    hints = _hints(tool)
    assert hints.get("context") is RunContext
    assert tool.info.name == "extract_dynamic_variables"


def test_extract_node_tool_merges_variables_and_advances_on_success() -> None:
    variables: dict = {}
    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    node = _extract_node()
    tool = make_extract_node_tool(
        node, variables=variables, state=state, on_transition=on_transition
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    _run(fnc({"plan": "PPO", "confirmed": True}, None))

    assert variables == {"plan": "PPO", "confirmed": "true"}
    assert state.collected_dynamic_variables == {"plan": "PPO", "confirmed": "true"}
    assert calls == [node["edges"][0]]


def test_extract_node_tool_empty_extraction_advances_the_else_edge() -> None:
    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    node = _extract_node()
    tool = make_extract_node_tool(node, variables={}, state=state, on_transition=on_transition)
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    _run(fnc({}, None))

    assert calls == [node["else_edge"]]


def test_extract_node_tool_two_prompt_edges_does_not_auto_advance_on_success() -> None:
    """`make_extract_node_tool` shares `make_function_node_tool`'s edge-choice
    rule: a non-empty extraction with more than one usable prompt edge must
    leave the choice to the model, not blindly follow ``edges[0]``.
    """
    variables: dict = {}
    state = CallState(call_id="call_1")
    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    node = _extract_node(
        edges=[
            {
                "id": "extracted-a",
                "transition_condition": _prompt_condition("Plan is PPO"),
                "destination_node_id": "n_ppo",
            },
            {
                "id": "extracted-b",
                "transition_condition": _prompt_condition("Plan is HMO"),
                "destination_node_id": "n_hmo",
            },
        ]
    )
    tool = make_extract_node_tool(
        node, variables=variables, state=state, on_transition=on_transition
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    _run(fnc({"plan": "PPO"}, None))

    assert variables == {"plan": "PPO"}
    assert calls == []


# ---------------------------------------------------------------------------
# make_flow_kb_lookup_tool
# ---------------------------------------------------------------------------


def test_flow_kb_lookup_tool_resolves_type_hints_and_passes_kb_config_through() -> None:
    knowledge = _FakeKnowledge({"results": [{"title": "T", "content": "C"}]})
    state = CallState(call_id="call_1")

    tool = make_flow_kb_lookup_tool(
        {"top_k": 3, "filter_score": 0.5},
        knowledge=knowledge,
        call_id="call_1",
        knowledge_base_ids=["kb_1"],
        variables={},
        state=state,
    )
    hints = _hints(tool)
    assert hints.get("context") is RunContext

    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    result = _run(fnc({"query": "What is covered?"}, None))

    assert knowledge.calls == [
        {
            "call_id": "call_1",
            "query": "What is covered?",
            "knowledge_base_ids": ["kb_1"],
            "category": None,
            "top_k": 3,
        }
    ]
    assert "T" in result and "C" in result


def test_flow_kb_lookup_tool_with_no_knowledge_bases_still_builds() -> None:
    knowledge = _FakeKnowledge()
    state = CallState(call_id="call_1")

    tool = make_flow_kb_lookup_tool(
        None,
        knowledge=knowledge,
        call_id="call_1",
        knowledge_base_ids=[],
        variables={},
        state=state,
    )
    assert tool is not None


# ---------------------------------------------------------------------------
# An equation-conditioned success edge is evaluated against the LIVE variables
# the call just merged, not followed because it happens to be the only one.
# ---------------------------------------------------------------------------


def _equation_function_node() -> dict:
    return _function_node(
        edges=[
            {
                "id": "approved",
                "transition_condition": {
                    "type": "equation",
                    "operator": "&&",
                    "equations": [{"left": "{{member_id}}", "operator": "==", "right": "M-1"}],
                },
                "destination_node_id": "n_success",
            }
        ]
    )


def _run_equation_node(response_body: dict) -> list[dict]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    calls: list[dict] = []

    async def on_transition(edge: dict) -> None:
        calls.append(edge)

    tool = make_function_node_tool(
        _equation_function_node(),
        _FLOW_TOOLS,
        http=_client(handler),
        function_secret="s",
        variables={},
        call_info=None,
        state=CallState(call_id="call_1"),
        on_transition=on_transition,
    )
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    _run(fnc({"call_first_name": "Jo"}, _FakeContext()))
    return calls


def test_function_node_equation_edge_fires_on_the_variable_the_call_just_set() -> None:
    """The tool's own response variable is what the equation reads: it is
    merged into `variables` before the edge is chosen, in the same handler."""
    calls = _run_equation_node({"memberId": "M-1"})
    assert [edge["id"] for edge in calls] == ["approved"]


def test_function_node_equation_edge_that_is_false_takes_the_else_edge() -> None:
    """HTTP 200 with a body reporting a different member is not an error
    result, so this used to auto-advance down the "approved" edge and make the
    else branch unreachable on success."""
    calls = _run_equation_node({"memberId": "M-404"})
    assert [edge["id"] for edge in calls] == ["failure"]
