"""Conversation-flow graph: indexing and load-time validation.

This is the decision layer for Retell conversation flows — a node graph
walked live during a call (see docs/AGENT_VERSIONING.md and
docs/ARCHITECTURE.md for where this fits). It holds no ``livekit`` import: the
worker's CI runs the dev-only dependency group, which deliberately skips the
heavy livekit-agents stack, and this module must stay importable there.
Pure stdlib plus ``arhiteq_worker.config`` only.

A malformed flow must be caught here, at load time, before a call starts —
not 90 seconds into a live call when a node walk hits a dead end. Any
structural problem raises ``FlowError`` naming the offending node id so
``main.py`` can abort the call cleanly instead of stalling it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from arhiteq_worker.config import ConversationFlowConfig

# Retell node types this worker knows how to execute. An unsupported type is
# rejected at load rather than discovered mid-call.
SUPPORTED_NODE_TYPES: frozenset[str] = frozenset(
    {
        "conversation",
        "subagent",
        "branch",
        "function",
        "transfer_call",
        "end",
        "extract_dynamic_variables",
    }
)

# The single-object edge fields a node may carry, in addition to the
# ``edges`` list. Real Retell nodes mix and match these five shapes; this
# tuple is the one place that list is spelled out.
_SINGLE_EDGE_FIELDS: tuple[str, ...] = (
    "else_edge",
    "edge",
    "always_edge",
    "skip_response_edge",
)


class FlowError(Exception):
    """Raised for any unusable conversation-flow graph.

    ``main.py`` lets this abort the call at start rather than let a bad
    graph become a dead end partway through a live call.
    """


def iter_node_edges(node: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield every edge a node carries, tagged with the field it came from.

    Real Retell nodes spread edges across five fields, each with different
    runtime meaning:

    - ``edges`` (list) — conditional transitions, offered to the model or
      evaluated as equations.
    - ``else_edge`` — guaranteed fallback (seen on ``branch`` and
      ``function`` nodes).
    - ``edge`` — the single failure edge (seen on ``transfer_call``).
    - ``always_edge`` — unconditional next.
    - ``skip_response_edge`` — transition without speaking.

    Yields ``(shape, edge)`` tuples where ``shape`` is the field name the
    edge came from, so callers don't have to re-inspect the node to recover
    it. Order is stable and deterministic: ``edges[]`` first in list order,
    then the single-edge fields in the order listed above. Exported so later
    tasks (flow execution, transition evaluation) don't have to re-enumerate
    this list.
    """
    edges = node.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict):
                yield "edges", edge
    for field_name in _SINGLE_EDGE_FIELDS:
        edge = node.get(field_name)
        if isinstance(edge, dict):
            yield field_name, edge


class FlowGraph:
    """An indexed, validated conversation-flow graph.

    Node lookups are id -> raw node dict (the wire shape from
    ``ConversationFlowConfig``), so later tasks read whatever fields they
    need directly off the dict without a parallel typed model to keep in
    sync with Retell's schema.
    """

    def __init__(self, nodes_by_id: dict[str, dict[str, Any]], start_node_id: str) -> None:
        self._nodes_by_id = nodes_by_id
        self._start_node_id = start_node_id

    @classmethod
    def from_config(cls, flow: ConversationFlowConfig) -> FlowGraph:
        nodes_by_id: dict[str, dict[str, Any]] = {}
        for node in flow.nodes:
            node_id = node.get("id")
            if isinstance(node_id, str) and node_id:
                nodes_by_id[node_id] = node
        for component in flow.components:
            for node in component.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if isinstance(node_id, str) and node_id:
                    if node_id in nodes_by_id:
                        raise FlowError(f"duplicate node id {node_id!r} in component nodes")
                    nodes_by_id[node_id] = node

        if not nodes_by_id:
            raise FlowError("conversation flow has no nodes")

        for node_id, node in nodes_by_id.items():
            node_type = node.get("type")
            if node_type not in SUPPORTED_NODE_TYPES:
                raise FlowError(f"node {node_id!r} has unsupported type {node_type!r}")
            for _shape, edge in iter_node_edges(node):
                destination = edge.get("destination_node_id")
                if destination and destination not in nodes_by_id:
                    raise FlowError(f"node {node_id!r} has an edge to missing node {destination!r}")

        if flow.start_node_id not in nodes_by_id:
            raise FlowError(f"start node {flow.start_node_id!r} not found in flow")

        return cls(nodes_by_id, flow.start_node_id)

    def node(self, node_id: str) -> dict[str, Any]:
        try:
            return self._nodes_by_id[node_id]
        except KeyError:
            raise FlowError(f"node {node_id!r} not found in flow") from None

    @property
    def start(self) -> dict[str, Any]:
        return self._nodes_by_id[self._start_node_id]

    @property
    def global_nodes(self) -> list[dict[str, Any]]:
        return [
            node
            for node in self._nodes_by_id.values()
            if (node.get("global_node_setting") or {}).get("condition")
        ]
