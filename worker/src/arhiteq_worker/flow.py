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

import logging
import math
import re
from collections.abc import Iterator, Mapping
from typing import Any

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.variables import resolve_template

logger = logging.getLogger("arhiteq-worker.flow")

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


# Comparison operators documented at
# https://docs.retellai.com/build/conversation-flow/transitions (equation
# conditions). ``exists`` is handled separately below since it is unary.
_NUMERIC_OPERATORS: frozenset[str] = frozenset({">", "<"})
_EQUALITY_OPERATORS: frozenset[str] = frozenset({"==", "!="})
_CONTAINMENT_OPERATORS: frozenset[str] = frozenset({"CONTAINS", "NOT CONTAINS"})

# A bare "{{name}}" operand, allowing surrounding whitespace, with nothing
# else in the operand. Used by `_resolve_operand` to decide when a real
# variables-mapping lookup (rather than a `resolve_template` pass) is the
# right way to detect "missing".
_SINGLE_PLACEHOLDER = re.compile(r"^\s*\{\{\s*([^{}]+?)\s*\}\}\s*$")

# Sentinel distinguishing "no such key" from a legitimately falsy value
# (None, "", 0) coming back from the variables mapping.
_NOT_FOUND = object()


def _as_float(text: str) -> float | None:
    try:
        value = float(text)
    except TypeError, ValueError:
        return None
    # "inf"/"-inf"/"nan" parse as floats but are not the numeric comparands
    # Retell equations mean; treat them as non-numeric so callers fall back
    # to string comparison instead of e.g. "inf" > 18 or nan != nan.
    return value if math.isfinite(value) else None


def _resolve_operand(value: Any, variables: Mapping[str, Any]) -> tuple[str, bool]:
    """Resolve one equation operand to text, reporting whether it is missing.

    ``variables`` supplies dynamic-variable values exactly as
    ``resolve_template`` would for a prompt, so ``{{current_time}}`` and other
    system variables resolve the same way here as they do in agent text.

    An operand that is *exactly* one placeholder (``{{name}}``, whitespace
    allowed inside the braces) is looked up directly in ``variables`` via its
    own ``.get`` — including ``ResolutionVariables``' lazily-computed system
    variables (``{{current_time}}``, ``{{session_duration}}``, ...), which
    only materialize through that mapping's ``__missing__``/``get`` machinery,
    not a plain ``dict`` key check. Absence of the key is "missing".

    Anything else — a literal with no placeholder, or text with an embedded
    placeholder alongside other characters — is resolved with
    ``resolve_template`` as before and treated as present. We used to treat a
    leftover ``{{...}}`` in the resolved text as "missing", but that is wrong:
    ``resolve_template`` never re-scans a substituted *value*, so a variable
    whose value legitimately contains ``{{`` (e.g. ``{"note": "Say {{hi}} to
    caller"}``) was mistaken for an unresolved placeholder even though the
    variable is present. The direct-lookup path above is what makes the
    common case — a bare variable compared against a literal — correct;
    embedded-placeholder text is rare enough in equation operands that we
    accept the "always present" simplification there.
    """
    if value is None:
        return "", False
    text = value if isinstance(value, str) else str(value)
    single = _SINGLE_PLACEHOLDER.match(text)
    if single:
        found = variables.get(single.group(1), _NOT_FOUND)
        if found is _NOT_FOUND:
            return "", True
        return (found if isinstance(found, str) else str(found)), False
    if "{{" not in text:
        return text, False
    return resolve_template(text, variables), False


def _evaluate_single_equation(equation: Any, variables: Mapping[str, Any]) -> bool:
    """Evaluate one ``{"left", "operator", "right"}`` equation. Never raises."""
    try:
        if not isinstance(equation, dict):
            return False
        operator = equation.get("operator")
        if not isinstance(operator, str):
            return False
        left = equation.get("left")

        if operator == "exists":
            left_text, missing = _resolve_operand(left, variables)
            return not missing and left_text != ""

        left_text, left_missing = _resolve_operand(left, variables)
        if left_missing:
            return False
        right_text, right_missing = _resolve_operand(equation.get("right"), variables)
        if right_missing:
            return False

        if operator in _NUMERIC_OPERATORS:
            left_num, right_num = _as_float(left_text), _as_float(right_text)
            if left_num is None or right_num is None:
                return False
            return left_num > right_num if operator == ">" else left_num < right_num

        if operator in _EQUALITY_OPERATORS:
            left_num, right_num = _as_float(left_text), _as_float(right_text)
            equal = (
                left_num == right_num
                if left_num is not None and right_num is not None
                else left_text == right_text
            )
            return equal if operator == "==" else not equal

        if operator in _CONTAINMENT_OPERATORS:
            # GUESS (like the outer {left,operator,right} shape guess in
            # `evaluate_equation_condition`'s docstring): Retell does not
            # specify CONTAINS semantics anywhere we can find. We implement
            # it as a raw Python substring test. Their only documented
            # example, `"New York, Los Angeles" CONTAINS {{user_location}}`,
            # reads just as naturally as list *membership* against a
            # comma-separated value — and under substring, a fragment of one
            # entry (e.g. `user_location == "York"`) also matches, which
            # membership semantics would reject. We keep substring anyway: it
            # is the more general reading, it still satisfies the documented
            # example for whole values, and its failure mode (over-matching a
            # fragment) is at least predictable. If a real Retell flow ever
            # shows membership semantics instead, this is the one place to
            # change.
            contains = right_text in left_text
            return contains if operator == "CONTAINS" else not contains

        # Unrecognized operator: malformed, not exceptional.
        logger.debug("unrecognized equation operator %r", operator)
        return False
    except Exception:
        logger.debug("failed to evaluate equation %r", equation, exc_info=True)
        return False


def evaluate_equation_condition(condition: Any, variables: Mapping[str, Any]) -> bool:
    """Evaluate a deterministic ``{"type": "equation", ...}`` transition condition.

    ``condition`` is ``{"type": "equation", "equations": [...], "operator": "||"
    | "&&"}`` per Retell's OpenAPI schema, which pins only this outer shape
    (and a 50-equation cap we do not enforce here — evaluating more is not
    unsafe, just unlikely). The per-equation shape
    (``{"left", "operator", "right"}``) is not pinned by that schema; see the
    module docstring in ``tests/test_flow_equations.py`` for why we read it
    that way and where to correct it if a real flow ever disagrees.

    Never raises: any malformed shape (missing/empty ``equations``, missing
    ``operator``, non-dict ``condition``, wrong ``type``) is False, logged at
    debug so it is diagnosable without being noisy in normal operation.
    """
    try:
        if not isinstance(condition, dict) or condition.get("type") != "equation":
            return False
        equations = condition.get("equations")
        if not isinstance(equations, list) or not equations:
            return False
        operator = condition.get("operator")
        if operator not in ("&&", "||"):
            return False
        results = [_evaluate_single_equation(equation, variables) for equation in equations]
        return all(results) if operator == "&&" else any(results)
    except Exception:
        logger.debug("failed to evaluate equation condition %r", condition, exc_info=True)
        return False
