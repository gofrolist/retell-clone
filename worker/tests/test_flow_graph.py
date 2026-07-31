"""Flow graph parsing, indexing and validation (no livekit stack)."""

import logging

import pytest

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.flow import FlowError, FlowGraph, iter_node_edges


def _graph(flow_dict: dict) -> FlowGraph:
    return FlowGraph.from_config(ConversationFlowConfig.from_dict(flow_dict))


def test_indexes_every_node_by_id(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    for node in prior_auth_flow["nodes"]:
        assert graph.node(node["id"])["type"] == node["type"]


def test_start_node_is_the_configured_one(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    assert graph.start["id"] == prior_auth_flow["start_node_id"]


def test_component_nodes_are_reachable_by_id(prior_auth_flow) -> None:
    """A destination_node_id may point into a subflow; it must still resolve."""
    component_nodes = [n for c in prior_auth_flow["components"] for n in c["nodes"]]
    assert component_nodes, "fixture is expected to carry a component"
    graph = _graph(prior_auth_flow)
    for node in component_nodes:
        assert graph.node(node["id"])["id"] == node["id"]


def test_global_nodes_are_collected(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    expected = {
        n["id"]
        for n in prior_auth_flow["nodes"]
        if (n.get("global_node_setting") or {}).get("condition")
    }
    assert {n["id"] for n in graph.global_nodes} == expected


def test_iter_node_edges_tags_each_shape_in_order() -> None:
    """A node carrying all five shapes yields them tagged, edges[] first."""
    node = {
        "id": "a",
        "type": "function",
        "edges": [
            {"id": "e1", "transition_condition": {"type": "prompt", "prompt": "x"}},
            {"id": "e2", "transition_condition": {"type": "prompt", "prompt": "y"}},
        ],
        "else_edge": {"id": "e3"},
        "edge": {"id": "e4"},
        "always_edge": {"id": "e5"},
        "skip_response_edge": {"id": "e6"},
    }
    assert [(shape, edge["id"]) for shape, edge in iter_node_edges(node)] == [
        ("edges", "e1"),
        ("edges", "e2"),
        ("else_edge", "e3"),
        ("edge", "e4"),
        ("always_edge", "e5"),
        ("skip_response_edge", "e6"),
    ]


def test_iter_node_edges_across_prior_auth_fixture_matches_all_five_shapes(
    prior_auth_flow,
) -> None:
    """Sanity-checks the shape tagging against the real 28-edge fixture."""
    nodes = list(prior_auth_flow["nodes"])
    for component in prior_auth_flow["components"]:
        nodes.extend(component["nodes"])

    counts_by_shape: dict[str, int] = {}
    without_destination = 0
    for node in nodes:
        for shape, edge in iter_node_edges(node):
            counts_by_shape[shape] = counts_by_shape.get(shape, 0) + 1
            if not edge.get("destination_node_id"):
                without_destination += 1

    assert sum(counts_by_shape.values()) == 28
    assert without_destination == 3
    assert counts_by_shape == {
        "edges": 20,
        "else_edge": 3,
        "edge": 1,
        "skip_response_edge": 4,
    }


def test_unsupported_node_type_is_rejected_at_load() -> None:
    with pytest.raises(FlowError, match="node-mcp"):
        _graph(
            {
                "start_node_id": "a",
                "nodes": [
                    {
                        "id": "a",
                        "type": "conversation",
                        "instruction": {"type": "prompt", "text": "hi"},
                    },
                    {"id": "node-mcp", "type": "mcp", "mcp_id": "m1", "mcp_tool_name": "t"},
                ],
            }
        )


def test_edge_to_a_missing_node_is_rejected_at_load() -> None:
    with pytest.raises(FlowError, match="ghost"):
        _graph(
            {
                "start_node_id": "a",
                "nodes": [
                    {
                        "id": "a",
                        "type": "conversation",
                        "instruction": {"type": "prompt", "text": "hi"},
                        "edges": [
                            {
                                "id": "e1",
                                "transition_condition": {"type": "prompt", "prompt": "x"},
                                "destination_node_id": "ghost",
                            }
                        ],
                    }
                ],
            }
        )


def test_component_node_id_colliding_with_a_main_node_is_rejected() -> None:
    """A component node must not silently overwrite a main node of the same id."""
    with pytest.raises(FlowError, match="dup"):
        _graph(
            {
                "start_node_id": "dup",
                "nodes": [
                    {
                        "id": "dup",
                        "type": "conversation",
                        "instruction": {"type": "prompt", "text": "hi"},
                    }
                ],
                "components": [
                    {
                        "nodes": [
                            {
                                "id": "dup",
                                "type": "end",
                            }
                        ]
                    }
                ],
            }
        )


def test_dangling_edge_without_a_destination_is_allowed() -> None:
    """Authored-but-unconnected edges exist in real flows; they must not abort a call."""
    graph = _graph(
        {
            "start_node_id": "a",
            "nodes": [
                {
                    "id": "a",
                    "type": "conversation",
                    "instruction": {"type": "prompt", "text": "hi"},
                    "always_edge": {
                        "id": "e1",
                        "transition_condition": {"type": "prompt", "prompt": "Always"},
                    },
                }
            ],
        }
    )
    assert graph.start["id"] == "a"


def test_missing_start_node_is_rejected() -> None:
    with pytest.raises(FlowError):
        _graph({"start_node_id": "nope", "nodes": [{"id": "a", "type": "end"}]})


def test_empty_graph_is_rejected() -> None:
    with pytest.raises(FlowError):
        _graph({"nodes": []})


def test_every_real_fixture_loads(request) -> None:
    from conftest import load_retell_flow_fixture

    for name in ("prior_auth_hotline.json", "clara_outbound.json", "identity_verify_transfer.json"):
        graph = _graph(load_retell_flow_fixture(name))
        assert graph.start is not None


# ---------------------------------------------------------------------------
# Reachability: a node nothing points at is a silent dead feature.
# ---------------------------------------------------------------------------


def test_a_node_nothing_points_at_is_reported(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        _graph(
            {
                "start_node_id": "a",
                "nodes": [
                    {"id": "a", "type": "conversation"},
                    {"id": "orphan", "type": "end"},
                ],
            }
        )
    assert "unreachable" in caplog.text
    assert "orphan" in caplog.text


def test_a_wired_up_graph_reports_nothing(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        _graph(
            {
                "start_node_id": "a",
                "nodes": [
                    {
                        "id": "a",
                        "type": "conversation",
                        "always_edge": {"id": "e1", "destination_node_id": "b"},
                    },
                    {"id": "b", "type": "end"},
                ],
            }
        )
    assert "unreachable" not in caplog.text


def test_a_global_node_counts_as_reachable_without_an_authored_edge(caplog) -> None:
    """`prompt_edges` synthesizes an edge into a global node from every node,
    so it is reachable by construction even with nothing pointing at it."""
    with caplog.at_level(logging.WARNING):
        _graph(
            {
                "start_node_id": "a",
                "nodes": [
                    {"id": "a", "type": "conversation"},
                    {
                        "id": "escalate",
                        "type": "end",
                        "global_node_setting": {"condition": "caller asks for a human"},
                    },
                ],
            }
        )
    assert "unreachable" not in caplog.text


def test_the_clara_fixture_is_reported_as_mostly_unreachable(caplog) -> None:
    """The real export that motivated the check: its start node's only edge is
    an ``always_edge`` with no destination, so all six function nodes are dead
    and the call parks on the welcome node with no tools."""
    from conftest import load_retell_flow_fixture

    with caplog.at_level(logging.WARNING):
        _graph(load_retell_flow_fixture("clara_outbound.json"))
    assert "10 of 11 nodes are unreachable" in caplog.text
