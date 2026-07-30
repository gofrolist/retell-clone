"""Edge selection and per-node instruction assembly (no livekit stack).

Covers the last piece of the pure decision layer: choosing which edge to
follow (`select_equation_edge`, `fallback_edge`), what the model is allowed to
choose from (`prompt_edges`, `transition_tool_schema`), and what it is told at
each node (`node_instructions`, `static_text`).
"""

from __future__ import annotations

from typing import Any

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.flow import (
    FlowGraph,
    fallback_edge,
    node_instructions,
    prompt_edges,
    select_equation_edge,
    static_text,
    transition_tool_schema,
)
from arhiteq_worker.variables import ResolutionVariables


def _graph(flow_dict: dict[str, Any]) -> FlowGraph:
    return FlowGraph.from_config(ConversationFlowConfig.from_dict(flow_dict))


def _eq(left: Any, operator: str, right: Any) -> dict[str, Any]:
    return {"left": left, "operator": operator, "right": right}


def _equation_condition(*equations: dict[str, Any]) -> dict[str, Any]:
    return {"type": "equation", "equations": list(equations), "operator": "&&"}


def _prompt_condition(text: str) -> dict[str, Any]:
    return {"type": "prompt", "prompt": text}


# ---------------------------------------------------------------------------
# select_equation_edge
# ---------------------------------------------------------------------------


def test_select_equation_edge_skips_prompt_edges_and_picks_first_true_in_order() -> None:
    node = {
        "id": "n1",
        "type": "branch",
        "edges": [
            {
                "id": "prompt-edge",
                "transition_condition": _prompt_condition("Anything"),
                "destination_node_id": "x",
            },
            {
                "id": "eq-false",
                "transition_condition": _equation_condition(_eq("{{age}}", ">", 100)),
                "destination_node_id": "x",
            },
            {
                "id": "eq-true-first",
                "transition_condition": _equation_condition(_eq("{{age}}", ">", 1)),
                "destination_node_id": "x",
            },
            {
                "id": "eq-true-second",
                "transition_condition": _equation_condition(_eq("{{age}}", ">", 2)),
                "destination_node_id": "x",
            },
        ],
    }
    edge = select_equation_edge(node, {"age": "30"})
    assert edge is not None
    assert edge["id"] == "eq-true-first"


def test_select_equation_edge_returns_none_when_nothing_fires() -> None:
    node = {
        "id": "n1",
        "type": "branch",
        "edges": [
            {
                "id": "prompt-edge",
                "transition_condition": _prompt_condition("Anything"),
                "destination_node_id": "x",
            },
            {
                "id": "eq-false",
                "transition_condition": _equation_condition(_eq("{{age}}", ">", 100)),
                "destination_node_id": "x",
            },
        ],
    }
    assert select_equation_edge(node, {"age": "30"}) is None


def test_select_equation_edge_never_follows_a_dangling_edge() -> None:
    """A true-firing equation edge with no destination must be skipped, not returned."""
    node = {
        "id": "n1",
        "type": "branch",
        "edges": [
            {
                "id": "eq-true-dangling",
                "transition_condition": _equation_condition(_eq("{{age}}", ">", 1)),
                # no destination_node_id
            },
            {
                "id": "eq-true-connected",
                "transition_condition": _equation_condition(_eq("{{age}}", ">", 1)),
                "destination_node_id": "x",
            },
        ],
    }
    edge = select_equation_edge(node, {"age": "30"})
    assert edge is not None
    assert edge["id"] == "eq-true-connected"


# ---------------------------------------------------------------------------
# prompt_edges
# ---------------------------------------------------------------------------


def test_prompt_edges_excludes_equation_and_dangling_edges() -> None:
    node = {
        "id": "n1",
        "type": "conversation",
        "edges": [
            {
                "id": "p1",
                "transition_condition": _prompt_condition("Provider office"),
                "destination_node_id": "x",
            },
            {
                "id": "p2-dangling",
                "transition_condition": _prompt_condition("Pharmacy"),
                # no destination_node_id: authored but unconnected
            },
            {
                "id": "eq1",
                "transition_condition": _equation_condition(_eq("{{age}}", ">", 1)),
                "destination_node_id": "x",
            },
        ],
    }
    edges = prompt_edges(node, [])
    assert [edge["id"] for edge in edges] == ["p1"]


def test_prompt_edges_appends_one_synthetic_entry_per_global_node() -> None:
    node = {"id": "n1", "type": "conversation", "edges": []}
    global_nodes = [
        {
            "id": "g1",
            "type": "branch",
            "global_node_setting": {"condition": "Caller wants a human"},
        },
        {
            "id": "g2",
            "type": "end",
            "global_node_setting": {"condition": "Caller wants to hang up"},
        },
    ]
    edges = prompt_edges(node, global_nodes)
    assert [edge["destination_node_id"] for edge in edges] == ["g1", "g2"]
    conditions = [edge["transition_condition"]["prompt"] for edge in edges]
    assert conditions == ["Caller wants a human", "Caller wants to hang up"]
    # Synthetic ids must be stable and collision-free against authored ids.
    ids = [edge["id"] for edge in edges]
    assert len(ids) == len(set(ids))
    assert prompt_edges(node, global_nodes)[0]["id"] == edges[0]["id"]


def test_prompt_edges_synthetic_id_never_collides_with_an_authored_id() -> None:
    node = {
        "id": "n1",
        "type": "conversation",
        "edges": [
            {
                "id": "p1",
                "transition_condition": _prompt_condition("Provider office"),
                "destination_node_id": "x",
            }
        ],
    }
    global_nodes = [{"id": "g1", "type": "end", "global_node_setting": {"condition": "Hang up"}}]
    edges = prompt_edges(node, global_nodes)
    ids = [edge["id"] for edge in edges]
    assert len(ids) == len(set(ids))
    assert "p1" in ids


def test_prompt_edges_does_not_offer_a_global_node_a_transition_to_itself() -> None:
    """A global node visited as a real node must not see itself as a candidate."""
    node = {
        "id": "g1",
        "type": "branch",
        "global_node_setting": {"condition": "Caller wants a human"},
        "edges": [],
    }
    global_nodes = [node]
    assert prompt_edges(node, global_nodes) == []


# ---------------------------------------------------------------------------
# fallback_edge
# ---------------------------------------------------------------------------


def test_fallback_edge_is_else_edge_for_branch_and_function() -> None:
    branch = {
        "id": "b1",
        "type": "branch",
        "else_edge": {"id": "else1", "destination_node_id": "x"},
    }
    function = {
        "id": "f1",
        "type": "function",
        "else_edge": {"id": "else2", "destination_node_id": "y"},
    }
    assert fallback_edge(branch)["id"] == "else1"
    assert fallback_edge(function)["id"] == "else2"


def test_fallback_edge_is_the_single_edge_for_transfer_call() -> None:
    node = {
        "id": "t1",
        "type": "transfer_call",
        "edge": {"id": "edge1", "destination_node_id": "z"},
    }
    assert fallback_edge(node)["id"] == "edge1"


def test_fallback_edge_is_none_for_a_bare_conversation_node() -> None:
    node = {
        "id": "c1",
        "type": "conversation",
        "instruction": {"type": "static_text", "text": "hi"},
    }
    assert fallback_edge(node) is None


def test_fallback_edge_is_none_when_the_fallback_is_dangling() -> None:
    """A fallback with no destination must never be followed."""
    node = {
        "id": "f1",
        "type": "function",
        "else_edge": {"id": "else1"},  # no destination_node_id
    }
    assert fallback_edge(node) is None


def test_fallback_edge_against_the_real_fixture_dangling_cases(prior_auth_flow) -> None:
    """The fixture has a dangling function else_edge and a dangling transfer_call edge."""
    graph = _graph(prior_auth_flow)
    dangling_function = graph.node("node-1773865257897")
    assert dangling_function["type"] == "function"
    assert fallback_edge(dangling_function) is None

    dangling_transfer = graph.node("node-1773866123876")
    assert dangling_transfer["type"] == "transfer_call"
    assert fallback_edge(dangling_transfer) is None


# ---------------------------------------------------------------------------
# node_instructions / static_text
# ---------------------------------------------------------------------------


def _flow_with_node(node: dict[str, Any], *, global_prompt: str = "") -> FlowGraph:
    return _graph(
        {
            "global_prompt": global_prompt,
            "start_node_id": node["id"],
            "nodes": [node],
        }
    )


def test_node_instructions_starts_with_the_global_prompt_and_resolves_variables() -> None:
    node = {
        "id": "n1",
        "type": "conversation",
        "instruction": {"type": "prompt", "text": "Ask for the {{field}}."},
    }
    flow = _flow_with_node(node, global_prompt="You are an agent for {{company}}.")
    result = node_instructions(node, flow, {"field": "date of birth", "company": "Acme"})
    assert result.startswith("You are an agent for Acme.")
    assert "Ask for the date of birth." in result
    assert "{{" not in result


def test_node_instructions_contains_prompt_node_instruction_text() -> None:
    node = {
        "id": "n1",
        "type": "conversation",
        "instruction": {"type": "prompt", "text": "Provide a natural variation of: hello"},
    }
    flow = _flow_with_node(node)
    result = node_instructions(node, flow, {})
    assert "Provide a natural variation of: hello" in result


def test_node_instructions_lists_transitions_with_ids() -> None:
    node = {
        "id": "n1",
        "type": "conversation",
        "instruction": {"type": "prompt", "text": "Ask where they are calling from."},
        "edges": [
            {
                "id": "edge-provider",
                "transition_condition": _prompt_condition("Provider office"),
                "destination_node_id": "x",
            },
            {
                "id": "edge-pharmacy",
                "transition_condition": _prompt_condition("Pharmacy"),
                "destination_node_id": "y",
            },
        ],
    }
    flow = _graph(
        {
            "start_node_id": "n1",
            "nodes": [
                node,
                {"id": "x", "type": "end"},
                {"id": "y", "type": "end"},
            ],
        }
    )
    result = node_instructions(node, flow, {})
    assert "edge-provider" in result
    assert "Provider office" in result
    assert "edge-pharmacy" in result
    assert "Pharmacy" in result


def test_node_instructions_for_static_text_node_does_not_ask_model_to_phrase() -> None:
    node = {
        "id": "n1",
        "type": "conversation",
        "instruction": {"type": "static_text", "text": "Please hold, {{first_name}}."},
    }
    flow = _flow_with_node(node)
    result = node_instructions(node, flow, {"first_name": "Jo"})
    # The verbatim line is static_text's job, not node_instructions'.
    assert "Please hold" not in result
    assert static_text(node, {"first_name": "Jo"}) == "Please hold, Jo."


def test_static_text_returns_none_for_a_prompt_node() -> None:
    node = {
        "id": "n1",
        "type": "conversation",
        "instruction": {"type": "prompt", "text": "Say hi"},
    }
    assert static_text(node, {}) is None


# ---------------------------------------------------------------------------
# transition_tool_schema
# ---------------------------------------------------------------------------


def test_transition_tool_schema_has_one_enum_entry_per_prompt_edge() -> None:
    node = {"id": "n1", "type": "conversation"}
    edges = [
        {
            "id": "edge-provider",
            "transition_condition": _prompt_condition("Provider office"),
            "destination_node_id": "x",
        },
        {
            "id": "edge-pharmacy",
            "transition_condition": _prompt_condition("Pharmacy"),
            "destination_node_id": "y",
        },
    ]
    schema = transition_tool_schema(node, edges)
    assert schema is not None
    assert schema["enum"] == ["edge-provider", "edge-pharmacy"]
    assert "Provider office" in schema["description"]
    assert "Pharmacy" in schema["description"]


def test_transition_tool_schema_is_none_with_no_prompt_edges() -> None:
    node = {"id": "n1", "type": "conversation"}
    assert transition_tool_schema(node, []) is None


# ---------------------------------------------------------------------------
# Real fixture: every conversation node assembles cleanly.
# ---------------------------------------------------------------------------


def test_node_instructions_over_every_conversation_node_of_the_real_fixture(
    prior_auth_flow,
) -> None:
    graph = _graph(prior_auth_flow)
    variables = ResolutionVariables({"member_id": "123", "date_of_birth": "2000-01-01"})

    nodes = list(prior_auth_flow["nodes"])
    for component in prior_auth_flow["components"]:
        nodes.extend(component["nodes"])
    conversation_nodes = [n for n in nodes if n["type"] == "conversation"]
    assert conversation_nodes, "fixture is expected to carry conversation nodes"

    for node in conversation_nodes:
        result = node_instructions(graph.node(node["id"]), graph, variables)
        assert isinstance(result, str)
        assert "{{" not in result
