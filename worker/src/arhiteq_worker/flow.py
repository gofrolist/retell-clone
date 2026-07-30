"""Conversation-flow graph: indexing, load-time validation, and node tools.

This is the decision layer for Retell conversation flows — a node graph
walked live during a call (see docs/AGENT_VERSIONING.md and
docs/ARCHITECTURE.md for where this fits). The graph/edge/instruction logic
above holds no ``livekit`` import: the worker's CI runs the dev-only
dependency group, which deliberately skips the heavy livekit-agents stack,
and this module must stay importable there. Pure stdlib plus
``arhiteq_worker.config``/``state``/``tools``/``variables`` only — the last of
those (``tools.py``) is itself livekit-free at import time (it only imports
``livekit.agents`` lazily, inside each of its own tool factories), so pulling
names from it here does not add a real livekit dependency.

The ``make_*_tool`` factories at the bottom of the file ARE the livekit-
touching part (Task 6 of the conversation-flow-runtime plan): they turn a
node's behaviour into an actual ``function_tool``, mirroring `tools.py`'s
existing constructors (``_make_transfer_call_tool`` et al.) — including their
placement convention of importing ``livekit.agents`` lazily, inside the
factory function itself, so this module's own import stays livekit-free and
its dev-only tests (``test_flow_transitions.py``, ``test_flow_runtime.py``)
keep working without the stack installed. Only ``test_flow_tools.py`` needs
livekit, and it skips itself (``pytest.importorskip``) when the stack is
absent.

A malformed flow must be caught here, at load time, before a call starts —
not 90 seconds into a live call when a node walk hits a dead end. Any
structural problem raises ``FlowError`` naming the offending node id so
``main.py`` can abort the call cleanly instead of stalling it.

NOTE: no ``from __future__ import annotations`` here, same reasoning as
`tools.py`'s own note — it would stringize the ``context: RunContext``
annotations the ``make_*_tool`` handlers carry, and livekit-agents resolves
those hints at *execution* time via ``typing.get_type_hints()`` against this
module's globals, where ``RunContext`` is deliberately not imported (the
dev-only tests run without the livekit stack). Python 3.14 (PEP 649)
evaluates the annotation lazily via the handler's closure instead, where the
factory's local import IS visible — but only when this future import is
absent to trigger PEP 563's eager stringizing.
"""

import asyncio
import json
import logging
import math
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Any

import httpx

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.state import CallState
from arhiteq_worker.tools import (
    KnowledgeSearch,
    _make_kb_lookup_tool,
    extract_variable_parameters,
    safe_execute_custom_tool,
    variable_to_string,
)
from arhiteq_worker.variables import resolve_deep, resolve_template

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


def flow_model_id(model_choice: Mapping[str, Any] | None) -> str:
    """The raw model id a flow's ``model_choice`` names, or ``""``.

    ``model_choice`` is ``{"type": "cascading", "model": "gpt-5.1",
    "high_priority": true}`` on the real fixtures — Retell's own catalogue, so
    the id is frequently an OpenAI one. This only *extracts* it; mapping it
    onto the Gemini catalogue is ``main.py``'s ``_gemini_model``, the same
    helper the single-prompt engine's ``llm.model`` goes through, so an OpenAI
    id can never reach a provider. Kept here (rather than inline in
    ``main.py``) so the extraction is testable without the livekit stack.

    ``""`` means "the flow named no model": callers fall back to the agent's
    own model, and from there to the deployment default.
    """
    if not isinstance(model_choice, Mapping):
        return ""
    model = model_choice.get("model")
    return model if isinstance(model, str) else ""


def start_speaker_for(node: dict[str, Any], default: str) -> str:
    """Who opens at *node*: its own ``start_speaker`` override, else *default*.

    A ``conversation`` node may carry its own ``start_speaker`` (see the design
    doc's per-node behaviour table); anything other than the two documented
    values is ignored rather than trusted, since it decides whether the agent
    opens the call or waits for the caller.
    """
    speaker = node.get("start_speaker")
    return speaker if speaker in ("agent", "user") else default


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


# What a `branch` node's classifier is told to answer when no option fits. It
# matches no edge id, so `match_edge_id` returns ``None`` and `FlowRuntime`
# falls through to the node's ``else_edge`` — the documented no-match route.
CLASSIFIER_NO_MATCH = "NONE"


def classifier_prompt(instructions: str, edges: list[dict[str, Any]]) -> str:
    """System prompt for the single classification call a ``branch`` node makes.

    A ``branch`` node speaks nothing and has no turn of its own, so the model
    is asked one closed question — pick an edge id — rather than being handed
    the node as a conversation. *instructions* is `node_instructions`' already
    resolved text (global prompt + the node's own instruction + the rendered
    transition list); the options are repeated here in the answer format the
    reply is parsed back out of.
    """
    options = "\n".join(
        f"{edge['id']}: {_condition_prompt_text(edge)}" for edge in edges if edge.get("id")
    )
    return (
        "You route a live phone conversation. Read the routing instructions and "
        "the conversation so far, then choose exactly one option id from the "
        "list below. Reply with the id alone and nothing else — no punctuation, "
        f"no explanation. Reply {CLASSIFIER_NO_MATCH} if none of them fit.\n\n"
        f"{instructions}\n\nOptions:\n{options}"
    )


def match_edge_id(answer: str, edges: list[dict[str, Any]]) -> str | None:
    """The edge id a classifier reply names, or ``None``.

    An exact match first; failing that, the longest id that appears anywhere in
    the reply, since models like to wrap the answer in quotes or a sentence.
    Longest-first matters when one id is a prefix of another. ``None`` covers
    both the explicit `CLASSIFIER_NO_MATCH` answer and unparseable prose, and
    `FlowRuntime` treats both the same: take the node's fallback edge.
    """
    text = (answer or "").strip()
    if not text:
        return None
    ids = [edge["id"] for edge in edges if isinstance(edge.get("id"), str) and edge["id"]]
    if text in ids:
        return text
    for edge_id in sorted(ids, key=len, reverse=True):
        if edge_id in text:
            return edge_id
    return None


# ---------------------------------------------------------------------------
# The livekit tool layer (Task 6): turning node behaviours into livekit tools.
# ---------------------------------------------------------------------------
#
# `on_transition` for `make_function_node_tool`/`make_extract_node_tool` takes
# the EDGE DICT to follow (matching `FlowRuntime.advance(edge: dict)`
# exactly), since the constructor itself already knows which edge that is —
# there is no id to resolve. `make_transition_tool`'s `on_transition` instead
# takes the edge id string the model named (there is no dict to hand over: the
# model can only name an id, per `transition_tool_schema`'s enum), so a caller
# wiring it to a real `FlowRuntime` must resolve that id back to an edge dict
# itself (e.g. from the same `edges` list `build_node_tools` was called
# with) before calling `advance`.


def _own_success_edges(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The node's own ``edges[]`` entries that are actually reachable on success.

    Excludes a dangling edge (no ``destination_node_id`` — authored but never
    wired up: there is nowhere to send the call) and a synthetic
    ``global::``-prefixed edge (`is_global_edge`) — a global node is reachable
    from *any* node, not something a "call succeeded" result routes into
    specifically, so it is never one of a node's own success continuations.

    Used by `make_function_node_tool`/`make_extract_node_tool` to decide how
    a success result should route — see `_select_success_edge`, and their own
    docstrings, for the actual rule.
    """
    return [
        edge
        for edge in node.get("edges") or []
        if isinstance(edge, dict) and edge.get("destination_node_id") and not is_global_edge(edge)
    ]


def _select_success_edge(
    success_edges: list[dict[str, Any]], failure_edge: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Which edge (if any) a *successful* function/extract result should follow.

    Real-fixture regression (``backend/tests/fixtures/retell_flows/
    prior_auth_hotline.json``): node ``node-1773865257897`` ("Get Member")
    carries TWO prompt edges — "Successfully get the member information" and
    "get_member has failed twice to get information" — plus an ``else_edge``,
    while node ``node-1773865358553`` ("Get Pa Cases") carries exactly ONE.
    The previous rule (unconditionally follow ``edges[0]`` on any non-error
    result) made the second "Get Member" edge unreachable forever: an HTTP
    failure went to ``else_edge``, and *any* HTTP 200 — including a body
    reporting "member not found" — went to ``edges[0]``, the "success" edge,
    even though the model was the only thing that could tell the two apart.

    The rule implemented here:

    - Exactly one usable success edge — unambiguous, no reason to spend a
      model turn — is followed automatically.
    - More than one usable success edge is ambiguous: this returns ``None``
      so the caller does *not* auto-advance, leaving the choice to the model
      through the ordinary ``transition_to`` mechanism, exactly as a
      ``conversation`` node works — the model just saw the tool result (or
      the extracted values) and can tell success from a soft failure that
      still returned normally.
    - Zero usable success edges falls back to *failure_edge* (``else_edge``):
      seen on ``clara_outbound.json``'s bare action nodes (``edges: []``,
      only an ``else_edge`` back to the flow's start node) — there is no
      success-specific continuation authored at all, so the guaranteed
      fallback is the only place to go.
    """
    if len(success_edges) == 1:
        return success_edges[0]
    if not success_edges:
        return failure_edge
    return None


def _is_error_result(result: str) -> bool:
    """Did a tool result string come back as Retell's ``{"error": ...}`` shape?

    Mirrors `flow_runtime._transfer_failed`'s convention for the same
    question about a different tool's result. Anything unparseable is *not*
    treated as an error here (unlike that sibling check) — an
    `execute_custom_tool` failure always reaches this as ``_tool_error``'s
    JSON envelope, never as free text, so unparseable text would mean the
    call actually succeeded with an unusual body.
    """
    try:
        payload = json.loads(result)
    except ValueError:
        return False
    return isinstance(payload, dict) and bool(payload.get("error"))


# Tool kinds a node can install, as returned by `node_tool_kinds`. Named
# constants rather than bare strings so the decision (made here, where it is
# testable without livekit) and the construction of the tools it names
# (`main.py`, which needs livekit) cannot drift apart.
TOOL_TRANSITION = "transition_to"
TOOL_FUNCTION = "function"
TOOL_EXTRACT = "extract_dynamic_variables"
TOOL_KB_LOOKUP = "kb_lookup"

# Node types whose own tool routes their result (`_select_success_edge`).
_ACTION_NODE_TYPES: frozenset[str] = frozenset({"function", "extract_dynamic_variables"})


def node_auto_advances(node: dict[str, Any]) -> bool:
    """Will *node*'s own action tool route the call onward by itself?

    True exactly when `make_function_node_tool` / `make_extract_node_tool`
    would find an edge to follow from their result — one usable success edge,
    or none plus a usable ``else_edge``. False for every other node type (they
    have no action tool), and false for the ambiguous case
    `_select_success_edge` deliberately leaves to the model.
    """
    if node.get("type") not in _ACTION_NODE_TYPES:
        return False
    return _select_success_edge(_own_success_edges(node), fallback_edge(node)) is not None


def node_tool_kinds(
    node: dict[str, Any], edges: list[dict[str, Any]], *, knowledge_base_ids: list[str]
) -> list[str]:
    """Which tools *node* installs, by kind, in install order.

    The whole "what goes on this node" decision, expressed over plain data so
    it stays testable in the dev-only environment; ``main.py`` only maps each
    kind to its `make_*_tool` constructor.

    - `TOOL_TRANSITION` when the node has prompt edges to offer
      (`transition_tool_schema` returns ``None`` when it has none) **and**
      nothing else is going to route it. That second half is the whole point:
      a ``function`` / ``extract_dynamic_variables`` node with more than one
      usable edge deliberately does *not* auto-advance — the model, which just
      saw the tool result, is the only thing that can tell those edges apart
      (`_select_success_edge`) — so it must be given a transition tool or it
      stalls with no way forward and the call dies there. This is why the rule
      is not "conversation nodes get a transition tool". Conversely, a node
      that *does* auto-advance is not given one: the model could otherwise
      transition instead of running the node's tool, or transition a second
      time along a stale edge after the tool already moved the call on.
    - `TOOL_FUNCTION` / `TOOL_EXTRACT` — the node's own action tool.
    - `TOOL_KB_LOOKUP` on ``conversation`` / ``subagent`` nodes when the flow
      carries ``knowledge_base_ids`` (`make_flow_kb_lookup_tool`'s documented
      attachment rule; a node with its own action tool is deliberately not
      given a competing one).
    """
    kinds: list[str] = []
    if transition_tool_schema(node, edges) is not None and not node_auto_advances(node):
        kinds.append(TOOL_TRANSITION)
    node_type = node.get("type")
    if node_type == "function":
        kinds.append(TOOL_FUNCTION)
    elif node_type == "extract_dynamic_variables":
        kinds.append(TOOL_EXTRACT)
    if knowledge_base_ids and node_type in ("conversation", "subagent"):
        kinds.append(TOOL_KB_LOOKUP)
    return kinds


def make_transition_tool(
    schema: dict[str, Any],
    on_transition: Callable[[str], Awaitable[None]],
    *,
    state: CallState,
) -> Any:
    """Wrap `transition_tool_schema`'s enum into the model-facing ``transition_to`` tool.

    One tool per node, named ``transition_to`` — the "single synthetic tool
    per node" the design doc describes — whose sole argument, also named
    ``transition_to`` (matching `transition_tool_schema`'s own docstring: "raw
    JSON schema for the ``transition_to`` argument"), is *schema* itself: an
    enum of this node's prompt-edge ids.

    The handler records the invocation/result on `state` the way every other
    tool in `tools.py` does (see ``_make_transfer_call_tool``), then hands the
    chosen id to *on_transition* — never raising even if that callback fails,
    since a bug in the graph walk must not kill the model's turn (compare
    `tools.execute_custom_tool`'s callers, which always convert a failure into
    a result string rather than an exception).
    """
    from livekit.agents import RunContext, function_tool

    name = "transition_to"
    tool_schema = {
        "type": "function",
        "name": name,
        "description": "Choose the caller's next conversational step.",
        "parameters": {
            "type": "object",
            "properties": {"transition_to": schema},
            "required": ["transition_to"],
        },
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        edge_id = str(raw_arguments.get("transition_to") or "")
        tool_call_id = state.add_tool_invocation(name, json.dumps({"transition_to": edge_id}))
        result = "ok"
        try:
            await on_transition(edge_id)
        except Exception:  # a graph-walk bug must not kill the model's turn
            logger.warning("transition_to %r failed", edge_id, exc_info=True)
            result = "failed to transition"
        state.add_tool_result(name, result, tool_call_id)
        return result

    return function_tool(handler, raw_schema=tool_schema)


def make_function_node_tool(
    node: dict[str, Any],
    flow_tools: list[dict[str, Any]],
    *,
    http: httpx.AsyncClient,
    function_secret: str,
    variables: dict[str, Any],
    call_info: Mapping[str, Any] | None,
    state: CallState,
    on_transition: Callable[[dict[str, Any]], Awaitable[None]],
) -> Any | None:
    """The action tool for a ``function`` node: run its tool, merge variables, advance.

    Resolves the node's ``tool_id`` against the flow's ``tools[]`` list —
    matched on ``tool_id``, a flow-scoped identifier unrelated to the node id
    or the tool's own display ``name`` — and executes it with the
    already-tested `safe_execute_custom_tool`. Passing it ``entry=entry`` and
    ``state=state`` means it *already* merges the entry's ``response_variables``
    into *variables* and records the invocation/result on `state` itself
    (`tools.py`'s tested behaviour, reused rather than re-implemented); there
    is nothing left to duplicate for either.

    Routing: the design doc pins only "failure routes to else_edge" — a hard
    failure (an error result) always follows `fallback_edge` (``else_edge``).
    A successful call follows `_select_success_edge` over the node's own
    `_own_success_edges`: exactly one usable success edge auto-advances (the
    unambiguous case, seen on ``prior_auth_hotline.json``'s "Get Pa Cases"
    node); more than one leaves the choice to the model instead of guessing
    (seen on that same fixture's "Get Member" node — see
    `_select_success_edge`'s docstring for why); zero falls back to
    ``else_edge`` (seen on ``clara_outbound.json``'s bare action nodes, whose
    ``edges: []`` carry no success-specific continuation at all).
    ``wait_for_result: false`` fires the request without waiting
    (`asyncio.create_task` — its ``response_variables`` land only if/when it
    later completes) and applies that same edge selection immediately, per
    task-6-brief.md's "advance without waiting for the result" — there is no
    result yet to call an error, so only the success side of the rule
    applies.

    Returns ``None`` — nothing to install — if ``tool_id`` does not resolve or
    the resolved entry has no ``url``, mirroring `tools.build_tools`'s
    "skip with a warning" policy for an unsupported/misconfigured tool.
    """
    tool_id = node.get("tool_id")
    entry = next(
        (t for t in flow_tools if isinstance(t, dict) and t.get("tool_id") == tool_id),
        None,
    )
    if not entry or not entry.get("url"):
        logger.warning(
            "function node %r: tool_id %r not found in flow tools", node.get("id"), tool_id
        )
        return None

    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "function_tool"
    schema = {
        "type": "function",
        "name": name,
        "description": resolve_template(entry.get("description") or "", variables),
        "parameters": resolve_deep(
            entry.get("parameters") or {"type": "object", "properties": {}}, variables
        ),
    }
    speak_during = bool(node.get("speak_during_execution"))
    execution_message = entry.get("execution_message_description") or ""
    # Same filler-selection rule as `tools._make_http_tool` — a function
    # node's tool entry is the same shape as a customer HTTP tool, so it may
    # carry the same execution-message fields even though neither real
    # fixture's entries happen to.
    filler = (
        resolve_template(execution_message, variables)
        if entry.get("execution_message_type") == "static_text" and execution_message
        else "One moment, let me check that."
    )
    speak_after = entry.get("speak_after_execution")
    wait_for_result = node.get("wait_for_result", True) is not False
    success_edges = _own_success_edges(node)
    failure_edge = fallback_edge(node)

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str | None:
        if speak_during:
            try:
                context.session.say(filler, add_to_chat_ctx=False)
            except Exception:  # filler speech must never break the tool
                logger.debug("speak_during_execution failed for %s", name, exc_info=True)

        async def run() -> str:
            return await safe_execute_custom_tool(
                http,
                name=name,
                url=entry["url"],
                args=raw_arguments,
                function_secret=function_secret,
                variables=variables,
                call_info=call_info,
                state=state,
                entry=entry,
            )

        if not wait_for_result:
            asyncio.create_task(run())
            edge = _select_success_edge(success_edges, failure_edge)
            if edge is not None:
                await on_transition(edge)
            return json.dumps({"result": "request sent"})

        result = await run()
        edge = (
            failure_edge
            if _is_error_result(result)
            else _select_success_edge(success_edges, failure_edge)
        )
        if edge is not None:
            await on_transition(edge)
        if speak_after is False:
            # Retell: speak_after_execution=false -> the agent does not
            # respond to the tool result (mirrors `tools._make_http_tool`).
            try:
                from livekit.agents.llm import StopResponse

                raise StopResponse()
            except ImportError:
                logger.debug("StopResponse unavailable; returning result for %s", name)
        return result

    return function_tool(handler, raw_schema=schema)


def make_extract_node_tool(
    node: dict[str, Any],
    *,
    variables: dict[str, Any],
    state: CallState,
    on_transition: Callable[[dict[str, Any]], Awaitable[None]],
) -> Any:
    """The model-facing tool for an ``extract_dynamic_variables`` node.

    Parameters come from the existing `extract_variable_parameters` — the
    node's own ``variables`` field is exactly the Retell variable-spec shape a
    built-in ``extract_dynamic_variable`` tool already consumes
    (``tools._make_extract_dynamic_variable_tool``), so it is passed straight
    through with no adapting. Extracted values merge into both the live
    *variables* mapping and ``state.collected_dynamic_variables``, exactly
    like that built-in.

    Routing: the design doc pins only "failure or empty extraction routes to
    else_edge" — an empty extraction (nothing the model supplied matched a
    declared variable) always follows `fallback_edge` (``else_edge``). A
    non-empty extraction shares `make_function_node_tool`'s
    `_select_success_edge` rule instead of blindly following ``edges[0]``:
    exactly one usable success edge auto-advances; more than one leaves the
    choice to the model (it just extracted the values and is the only thing
    that can tell which continuation they call for); zero falls back to
    ``else_edge`` — see `_select_success_edge`'s docstring for the real
    ``function``-node fixture that motivates this. No real
    ``extract_dynamic_variables`` fixture node has more than one prompt edge
    today, but this constructor built its routing on the same
    `_own_success_edges` helper as `make_function_node_tool`, so it shared
    the single-edge assumption and gets the same fix.
    """
    from livekit.agents import RunContext, function_tool

    name = "extract_dynamic_variables"
    parameters = extract_variable_parameters(node)
    schema = {
        "type": "function",
        "name": name,
        "description": "Extract variables from the conversation as soon as they are known.",
        "parameters": parameters,
    }
    known = set(parameters["properties"])
    success_edges = _own_success_edges(node)
    failure_edge = fallback_edge(node)

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        extracted = {
            key: variable_to_string(value)
            for key, value in raw_arguments.items()
            if key in known and value is not None
        }
        tool_call_id = state.add_tool_invocation(name, json.dumps(extracted))
        if isinstance(variables, dict):
            # Same mapping the session resolves {{var}} against — extracted
            # values are immediately usable in prompts, tool configs, and
            # equation edges.
            variables.update(extracted)
        state.collected_dynamic_variables.update(extracted)
        result = json.dumps({"result": "variables extracted", "extracted": extracted})
        state.add_tool_result(name, result, tool_call_id)
        edge = _select_success_edge(success_edges, failure_edge) if extracted else failure_edge
        if edge is not None:
            await on_transition(edge)
        return result

    return function_tool(handler, raw_schema=schema)


def make_flow_kb_lookup_tool(
    kb_config: Mapping[str, Any] | None,
    *,
    knowledge: KnowledgeSearch,
    call_id: str,
    knowledge_base_ids: list[str],
    variables: Mapping[str, Any],
    state: CallState,
) -> Any:
    """kb_lookup tool for a flow's ``conversation``/``subagent`` nodes.

    Reuses `tools._make_kb_lookup_tool` (the tested single-prompt
    implementation) rather than a second one — a caller building this only
    needs to decide *when* to attach it (every ``conversation``/``subagent``
    node, when the flow carries ``knowledge_base_ids`` — a decision left to
    the caller closing over the flow config, per the design doc; nothing
    about that dispatch belongs here).

    *kb_config* is the flow's ``{top_k, filter_score}``. Only ``top_k`` has
    anywhere to go today: `KnowledgeSearch.search_knowledge_base`
    (``internal_api.py``) has no ``filter_score`` parameter yet, so it rides
    along on the synthetic entry below for forward compatibility, but nothing
    downstream reads it until that protocol grows one.
    """
    entry: dict[str, Any] = {"name": "kb_lookup"}
    config = kb_config or {}
    if config.get("top_k") is not None:
        entry["top_k"] = config["top_k"]
    if config.get("filter_score") is not None:
        entry["filter_score"] = config["filter_score"]
    return _make_kb_lookup_tool(
        entry,
        knowledge=knowledge,
        call_id=call_id,
        knowledge_base_ids=knowledge_base_ids,
        variables=variables,
        state=state,
    )
