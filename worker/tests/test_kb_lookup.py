"""kb_lookup: argument schema, result shaping, and build_tools dispatch.

The pure helpers run without the livekit stack; the dispatch/handler tests
need it and are skipped in the dev-only env (same split as
test_tool_annotations.py).
"""

import asyncio
import json

import httpx
import pytest

from arhiteq_worker.state import CallState
from arhiteq_worker.tools import (
    KB_LOOKUP_NO_RESULTS,
    build_tools,
    kb_lookup_parameters,
    kb_lookup_result,
)

# The consumer's imported declaration (retell/{sales,inbound}/kb_lookup.json).
CONSUMER_ENTRY = {
    "type": "kb_lookup",
    "name": "kb_lookup",
    "description": "Looks up a factual answer from the USANRetirement knowledge base.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The caller's question."},
            "category": {
                "type": "string",
                "enum": ["company", "product", "emergency", "local", "meds", "faq"],
                "description": "Category to narrow search.",
            },
        },
        "required": ["query"],
    },
}


# --- argument schema -------------------------------------------------------


def test_declared_parameters_are_kept_verbatim() -> None:
    """A consumer's category enum steers retrieval — it must survive."""
    schema = kb_lookup_parameters(CONSUMER_ENTRY)
    assert schema["properties"]["category"]["enum"] == [
        "company",
        "product",
        "emergency",
        "local",
        "meds",
        "faq",
    ]
    assert schema["required"] == ["query"]


def test_declared_parameters_are_not_mutated() -> None:
    entry = {"parameters": {"type": "object", "properties": {"topic": {"type": "string"}}}}
    before = json.dumps(entry)
    kb_lookup_parameters(entry)
    assert json.dumps(entry) == before, "tool config is shared; it must not be edited in place"


def test_query_is_added_to_a_declaration_that_omits_it() -> None:
    schema = kb_lookup_parameters(
        {"parameters": {"type": "object", "properties": {"topic": {"type": "string"}}}}
    )
    assert "query" in schema["properties"]
    assert "query" in schema["required"]


def test_default_schema_when_nothing_is_declared() -> None:
    schema = kb_lookup_parameters({})
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "category"}


# --- result shaping --------------------------------------------------------


def test_result_keeps_only_what_the_model_needs() -> None:
    shaped = json.loads(
        kb_lookup_result(
            {
                "results": [
                    {
                        "title": "Pricing",
                        "content": "$29 per month.",
                        "score": 1.2,
                        "source_id": "src_1",
                        "knowledge_base_id": "know_1",
                    }
                ]
            }
        )
    )
    assert shaped == {"results": [{"title": "Pricing", "content": "$29 per month."}]}


def test_non_ascii_content_is_not_escaped() -> None:
    """Live and simulated results must read the same.

    The simulation harness shapes its own kb_lookup result with
    ensure_ascii=False; escaping here would show an operator "informaci\\u00f3n"
    in the tool-call timeline for a live call and the real word in a simulation
    of the very same case.
    """
    shaped = kb_lookup_result(
        {"results": [{"title": "Información", "content": "El plan cuesta $29 al mes."}]}
    )
    assert "Información" in shaped
    assert "\\u" not in shaped


def test_empty_results_tell_the_agent_not_to_guess() -> None:
    shaped = json.loads(kb_lookup_result({"results": []}))
    assert shaped["results"] == []
    assert shaped["message"] == KB_LOOKUP_NO_RESULTS
    # A malformed payload is treated as a miss, not as an unhandled error.
    assert json.loads(kb_lookup_result({}))["message"] == KB_LOOKUP_NO_RESULTS


# --- dispatch + handler ----------------------------------------------------


class _Control:
    async def end_call(self, reason: str = "agent_hangup", *, flush_grace: bool = False) -> None:
        pass

    async def transfer_call(self, number: str) -> str:
        return "ok"

    async def press_digit(self, digits: str) -> None:
        pass

    async def agent_swap(self, agent_id: str, entry: dict) -> str:
        return "ok"


class _Knowledge:
    """Records the query the tool sends and replays a canned payload."""

    def __init__(self, payload: dict | None = None, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._payload = payload if payload is not None else {"results": []}
        self._fail = fail

    async def search_knowledge_base(
        self,
        call_id: str,
        query: str,
        *,
        knowledge_base_ids: list[str] | None = None,
        category: str | None = None,
        top_k: int | None = None,
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
        if self._fail:
            raise httpx.ConnectError("control plane down")
        return self._payload


def _build(entry: dict, knowledge=None, **kwargs) -> list:
    return build_tools(
        [entry],
        http=httpx.AsyncClient(),
        function_secret="s",
        variables=kwargs.pop("variables", {}),
        control=_Control(),
        state=kwargs.pop("state", CallState(call_id="call_x")),
        knowledge=knowledge,
        call_id=kwargs.pop("call_id", "call_x"),
        knowledge_base_ids=kwargs.pop("knowledge_base_ids", ["know_llm"]),
    )


def _invoke(tool, arguments: dict) -> str:
    fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
    return asyncio.run(fnc(arguments, None))


def test_kb_lookup_is_skipped_without_a_knowledge_client() -> None:
    """Better no tool than one the model calls and that always errors."""
    assert _build(CONSUMER_ENTRY, knowledge=None) == []
    assert _build(CONSUMER_ENTRY, knowledge=_Knowledge(), call_id="") == []


def test_handler_queries_and_shapes_the_result() -> None:
    pytest.importorskip("livekit.agents")
    knowledge = _Knowledge({"results": [{"title": "Pricing", "content": "$29 per month."}]})
    state = CallState(call_id="call_x")
    tool = _build(CONSUMER_ENTRY, knowledge=knowledge, state=state)[0]

    result = json.loads(_invoke(tool, {"query": "how much", "category": "product"}))

    assert result["results"] == [{"title": "Pricing", "content": "$29 per month."}]
    assert knowledge.calls == [
        {
            "call_id": "call_x",
            "query": "how much",
            "knowledge_base_ids": ["know_llm"],
            "category": "product",
            "top_k": None,
        }
    ]
    # The lookup lands in the transcript like any other tool call, so it shows
    # up in the dashboard's tool-call timeline.
    recorded = json.dumps(state.items, default=str)
    assert "kb_lookup" in recorded
    assert "Pricing" in recorded


def test_tool_config_narrows_the_knowledge_bases() -> None:
    pytest.importorskip("livekit.agents")
    knowledge = _Knowledge()
    entry = {**CONSUMER_ENTRY, "knowledge_base_ids": ["know_specific"]}
    _invoke(_build(entry, knowledge=knowledge)[0], {"query": "hello"})
    assert knowledge.calls[0]["knowledge_base_ids"] == ["know_specific"]


def test_variables_resolve_in_the_query() -> None:
    pytest.importorskip("livekit.agents")
    knowledge = _Knowledge()
    tool = _build(CONSUMER_ENTRY, knowledge=knowledge, variables={"state": "Florida"})[0]
    _invoke(tool, {"query": "services in {{state}}"})
    assert knowledge.calls[0]["query"] == "services in Florida"


def test_an_empty_query_is_rejected_without_a_lookup() -> None:
    pytest.importorskip("livekit.agents")
    knowledge = _Knowledge()
    result = json.loads(_invoke(_build(CONSUMER_ENTRY, knowledge=knowledge)[0], {"query": "  "}))
    assert "error" in result
    assert knowledge.calls == []


def test_no_attached_bases_searches_nothing_rather_than_falling_back() -> None:
    """An agent with no knowledge bases must not read another agent's.

    Sending an empty list makes the control plane fall back to the knowledge
    bases of the agent the CALL was created with — after an agent_swap, the
    agent we swapped away from. A destination agent configured with kb_lookup
    but no bases of its own would then answer out of the previous agent's KB.
    """
    pytest.importorskip("livekit.agents")
    knowledge = _Knowledge()
    tool = _build(CONSUMER_ENTRY, knowledge=knowledge, knowledge_base_ids=[])[0]

    result = json.loads(_invoke(tool, {"query": "how much"}))

    assert knowledge.calls == [], "no bases configured means no lookup at all"
    assert result["results"] == []
    assert result["message"] == KB_LOOKUP_NO_RESULTS


def test_a_control_plane_failure_becomes_a_tool_error() -> None:
    """A dead control plane must not kill the turn — the model gets a string."""
    pytest.importorskip("livekit.agents")
    result = json.loads(
        _invoke(_build(CONSUMER_ENTRY, knowledge=_Knowledge(fail=True))[0], {"query": "how much"})
    )
    assert "error" in result
    assert "ConnectError" in result["error"]
