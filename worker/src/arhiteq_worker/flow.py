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
    - ``skip_response_edge`` — speak the node's own line as usual, then
      advance without waiting for the caller's response.

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

    def __init__(
        self,
        nodes_by_id: dict[str, dict[str, Any]],
        start_node_id: str,
        global_prompt: str = "",
    ) -> None:
        self._nodes_by_id = nodes_by_id
        self._start_node_id = start_node_id
        self._global_prompt = global_prompt

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

        return cls(nodes_by_id, flow.start_node_id, flow.global_prompt)

    def node(self, node_id: str) -> dict[str, Any]:
        try:
            return self._nodes_by_id[node_id]
        except KeyError:
            raise FlowError(f"node {node_id!r} not found in flow") from None

    @property
    def start(self) -> dict[str, Any]:
        return self._nodes_by_id[self._start_node_id]

    @property
    def global_prompt(self) -> str:
        return self._global_prompt

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


# Prefix for a global node's synthetic prompt edge id (see `prompt_edges`).
# Authored Retell edge ids look like ``edge-1`` or ``edge-<timestamp>-<rand>``;
# this prefix lives in a namespace real ids never use, so it cannot collide,
# and it is derived only from the global node's own id, so it is stable
# across calls to `prompt_edges` for the same flow.
_GLOBAL_EDGE_ID_PREFIX = "global::"


def is_global_edge(edge: dict[str, Any]) -> bool:
    """Is *edge* one of `prompt_edges`'s synthetic ``global::``-prefixed edges?

    The one place that checks this by id prefix, so no second module ever
    hardcodes the ``"global::"`` literal. Used by `flow_runtime`'s stranding
    guard to tell a node's own authored edges apart from the synthetic ones
    every node is offered for every global node in the flow.
    """
    edge_id = edge.get("id")
    return isinstance(edge_id, str) and edge_id.startswith(_GLOBAL_EDGE_ID_PREFIX)


# The single-edge shapes that can hold a node's guaranteed fallback, in
# priority order (matches the order `iter_node_edges` yields them in).
_FALLBACK_SHAPES: frozenset[str] = frozenset({"else_edge", "edge"})


def select_equation_edge(
    node: dict[str, Any],
    variables: Mapping[str, Any],
    *,
    exclude_edge: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """First ``equation``-condition edge, in declaration order, whose condition is true.

    Equations are resolved deterministically before the model is ever asked
    to pick a transition, so ``prompt``-condition edges are ignored here
    entirely — this only ever returns an ``equation`` edge or ``None``. A
    dangling edge (no ``destination_node_id``) is never returned even if its
    condition is true: there is nowhere to send the call, so evaluation
    continues past it rather than stopping the walk.

    *exclude_edge*, when given, is skipped by identity (``is``, not value
    equality). `FlowRuntime` uses this to ask "does some *other* equation
    edge fire ahead of this node's own ``skip_response_edge``?" without the
    skip edge answering its own question: a ``skip_response_edge`` authored
    with an ``equation`` condition (unusual, but not disallowed) would
    otherwise come back from this same scan, reading as "an equation edge
    beat the skip edge" when it and the skip edge are the very same edge.
    """
    for _shape, edge in iter_node_edges(node):
        if exclude_edge is not None and edge is exclude_edge:
            continue
        condition = edge.get("transition_condition")
        if not isinstance(condition, dict) or condition.get("type") != "equation":
            continue
        if not edge.get("destination_node_id"):
            continue
        if evaluate_equation_condition(condition, variables):
            return edge
    return None


def prompt_edges(node: dict[str, Any], global_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The node's own ``prompt``-condition edges, plus one synthetic edge per global node.

    Global nodes (``global_node_setting.condition``) are reachable from
    anywhere without an authored edge, so each becomes a synthetic edge here
    shaped just like a real one — ``{"id", "transition_condition", "destination_node_id"}``
    — keyed by a `_GLOBAL_EDGE_ID_PREFIX`-prefixed id derived only from the
    global node's own id (stable across calls, and cannot collide with an
    authored edge id). A global node is never synthesized against itself:
    if *node* itself is one of ``global_nodes`` (a global node is a real,
    visitable node, not just an entry in other nodes' lists), offering it a
    transition to itself would be meaningless.

    Dangling authored edges (no ``destination_node_id``) are excluded: they
    must never be offered to the model since there is nowhere to send it if
    chosen.

    ``always_edge`` and ``skip_response_edge`` are excluded by shape,
    regardless of their own ``transition_condition``: both are the runtime's
    own, taken automatically (`FlowRuntime`), never a choice put to the
    model.
    """
    own_id = node.get("id")
    edges: list[dict[str, Any]] = []
    for shape, edge in iter_node_edges(node):
        if shape in ("always_edge", "skip_response_edge"):
            continue
        condition = edge.get("transition_condition")
        if not isinstance(condition, dict) or condition.get("type") != "prompt":
            continue
        if not edge.get("destination_node_id"):
            continue
        edges.append(edge)
    for global_node in global_nodes:
        global_id = global_node.get("id")
        if not isinstance(global_id, str) or not global_id or global_id == own_id:
            continue
        condition_text = (global_node.get("global_node_setting") or {}).get("condition")
        edges.append(
            {
                "id": f"{_GLOBAL_EDGE_ID_PREFIX}{global_id}",
                "transition_condition": {
                    "type": "prompt",
                    "prompt": condition_text if isinstance(condition_text, str) else "",
                },
                "destination_node_id": global_id,
            }
        )
    return edges


def fallback_edge(node: dict[str, Any]) -> dict[str, Any] | None:
    """The node's guaranteed non-prompt fallback: ``else_edge``, else the single ``edge``.

    Seen on ``branch``/``function`` (``else_edge``) and ``transfer_call``
    (``edge``, its lone failure edge). A node with neither shape (e.g. a bare
    ``conversation`` node) has no fallback: ``None``. A dangling fallback (no
    ``destination_node_id``) is also reported as ``None`` rather than the raw
    edge — the real ``prior_auth_hotline.json`` fixture has exactly this case
    (a ``function`` node's ``else_edge`` and a ``transfer_call`` node's
    ``edge`` both authored with no destination) and there is nowhere to send
    the call if it fires, so callers must not try to follow it.
    """
    for shape, edge in iter_node_edges(node):
        if shape in _FALLBACK_SHAPES:
            return edge if edge.get("destination_node_id") else None
    return None


def static_text(node: dict[str, Any], variables: Mapping[str, Any]) -> str | None:
    """The resolved verbatim line for a ``static_text`` node instruction, else ``None``."""
    instruction = node.get("instruction")
    if not isinstance(instruction, dict) or instruction.get("type") != "static_text":
        return None
    text = instruction.get("text")
    if not isinstance(text, str):
        return None
    return resolve_template(text, variables)


def _condition_prompt_text(edge: dict[str, Any]) -> str:
    condition = edge.get("transition_condition")
    if not isinstance(condition, dict):
        return ""
    prompt = condition.get("prompt")
    return prompt if isinstance(prompt, str) else ""


def _render_transition_list(edges: list[dict[str, Any]], variables: Mapping[str, Any]) -> str:
    lines = [
        f"- {edge.get('id')}: {resolve_template(_condition_prompt_text(edge), variables)}"
        for edge in edges
    ]
    return "\n".join(lines)


def node_instructions(node: dict[str, Any], flow: FlowGraph, variables: Mapping[str, Any]) -> str:
    """Assemble what the model sees at *node*: global prompt, instruction, transitions.

    In order: the flow's ``global_prompt``; the node's own ``instruction.text``
    when it is a ``prompt`` (a ``static_text`` node is spoken verbatim by
    `static_text` instead and contributes no phrasing instruction here); then
    a rendered list of every available transition — the node's own prompt
    edges plus one synthetic entry per global node, from `prompt_edges` —
    naming each edge's id beside its (resolved) condition text, because the
    model's tool call picks a transition by id and cannot choose reliably if
    it cannot see them. Every piece is passed through `resolve_template`.
    """
    parts = [resolve_template(flow.global_prompt, variables)]

    instruction = node.get("instruction")
    if isinstance(instruction, dict) and instruction.get("type") == "prompt":
        text = instruction.get("text")
        if isinstance(text, str):
            parts.append(resolve_template(text, variables))

    edges = prompt_edges(node, flow.global_nodes)
    if edges:
        parts.append("Available transitions:\n" + _render_transition_list(edges, variables))

    return "\n\n".join(parts)


def transition_tool_schema(
    node: dict[str, Any], edges: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Raw JSON schema for the ``transition_to`` argument, or ``None`` with no prompt edges.

    An enum of edge ids — the model must name one exactly, since that id is
    how the flow walker resolves the destination — with each condition's
    (unresolved; this function has no ``variables`` to resolve against)
    prompt text next to its id in the description. This returns only the
    parameter schema, not a callable tool: wrapping it (and resolving
    templates in the description) is Task 6's job, so this module — and its
    CI-installed dev-only tests — never has to import ``livekit``.
    """
    ids = [edge["id"] for edge in edges if edge.get("id")]
    if not ids:
        return None
    lines = [f"{edge['id']}: {_condition_prompt_text(edge)}" for edge in edges if edge.get("id")]
    node_name = node.get("name")
    prefix = (
        f"Transition options for node {node_name!r}. "
        if isinstance(node_name, str) and node_name
        else ""
    )
    return {
        "type": "string",
        "enum": ids,
        "description": prefix
        + "Choose the id matching the caller's response:\n"
        + "\n".join(lines),
    }
