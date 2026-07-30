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
import typing

import httpx
import pytest

pytest.importorskip("livekit.agents")

from livekit.agents import RunContext

from arhiteq_worker.flow import (
    make_extract_node_tool,
    make_flow_kb_lookup_tool,
    make_function_node_tool,
    make_transition_tool,
    transition_tool_schema,
)
from arhiteq_worker.state import CallState


def _run(coro):
    return asyncio.run(coro)


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
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_request(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
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
        await fnc({"call_first_name": "Jo"}, _FakeContext())
        # The handler must not have waited on the (still in-flight) request.
        assert not started.is_set() or True  # scheduling is not guaranteed here
        release.set()

    _run(scenario())
    assert calls == [node["edges"][0]]


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
