"""Walk a conversation-flow graph during a live call.

`arhiteq_worker.flow` is the pure decision layer (indexing, equations, edge
selection, prompt assembly). This module is the driver on top of it: it holds
"where the call is right now", enters nodes, and follows edges.

Like `flow`, it imports no ``livekit``: the worker's CI installs the dev-only
dependency group, so anything that touches the agent session — updating the
model's instructions, installing tools, speaking, hanging up, transferring —
arrives as an **injected callable**. `main.py` (Task 7) supplies the real ones;
tests supply recording fakes. If this module ever needs to import ``livekit``,
the seam is in the wrong place.

Routing rule that matters: a destination is always taken from an edge's own
``destination_node_id``. Edge ids are never node ids — `flow.prompt_edges`
synthesizes ``global::<node id>`` edges for global nodes, and looking that up
as a node would raise.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.flow import (
    FlowError,
    FlowGraph,
    fallback_edge,
    node_instructions,
    prompt_edges,
    select_equation_edge,
    static_text,
)
from arhiteq_worker.variables import resolve_template

logger = logging.getLogger("arhiteq-worker.flow")

# How many transitions may be taken in a row without a user turn in between.
# A flow may legitimately cycle (``wrap up`` -> branch -> ``wrap up``), and
# branch / skip_response nodes transition without waiting for a user turn
# (branch nodes speak nothing; skip_response nodes speak, then advance
# anyway), so a cycle made only of such nodes would otherwise spin forever
# inside one turn and hang the call. Twenty is far more than any authored
# flow chains automatically, and every user turn refreshes the budget, so a
# legitimate long walk is never cut short mid-call.
MAX_AUTOMATIC_TRANSITIONS = 20

# Reason recorded when a flow ``end`` node hangs up; matches the built-in
# end_call tool so call records stay consistent between the two paths.
HANGUP_REASON = "agent_hangup"

# Same shape the built-in transfer tool enforces: a destination that reaches
# the SIP leg must be strict E.164 unless the node opts out.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

SetInstructions = Callable[[str], Awaitable[None]]
SetTools = Callable[[list[Any]], Awaitable[None]]
Say = Callable[[str], Awaitable[None]]
Classify = Callable[[str, list[dict[str, Any]]], Awaitable[str | None]]
BuildNodeTools = Callable[[dict[str, Any], list[dict[str, Any]]], list[Any]]
EndCall = Callable[[str], Awaitable[None]]
TransferCall = Callable[[str], Awaitable[str]]


def _transfer_failed(result: Any) -> bool:
    """Did a `transfer_call` return value report failure?

    `main.py`'s call control returns a JSON string — ``{"result": ...}`` on
    success, ``{"error": ...}`` when the transfer could not be attempted (no
    SIP participant, for instance). Anything unparseable is treated as
    success rather than invented failure; an empty return says nothing came
    back at all and counts as failure.
    """
    if not isinstance(result, str) or not result.strip():
        return True
    try:
        payload = json.loads(result)
    except ValueError:
        return False
    return isinstance(payload, dict) and bool(payload.get("error"))


class FlowRuntime:
    """Drives one call through one conversation flow.

    Constructed once per call with the indexed *graph*, its *config* (start
    node and start speaker), the **live** *variables* mapping — read at every
    decision point, so a variable extracted mid-call changes the next
    equation result — and one callable per side effect.

    Decision order at every transition point is fixed: the current node's
    ``equation`` edges first, in declaration order, first true wins; only if
    none fires do ``prompt`` edges reach the model. Equation edges are never
    offered to the model, and ``always_edge`` / ``skip_response_edge`` are
    never offered either (they are the runtime's own, not choices).
    """

    def __init__(
        self,
        graph: FlowGraph,
        config: ConversationFlowConfig,
        variables: Mapping[str, Any],
        *,
        set_instructions: SetInstructions,
        set_tools: SetTools,
        say: Say,
        classify: Classify,
        build_node_tools: BuildNodeTools,
        end_call: EndCall,
        transfer_call: TransferCall,
        call_id: str = "",
    ) -> None:
        self._graph = graph
        self._config = config
        self._variables = variables
        self._set_instructions = set_instructions
        self._set_tools = set_tools
        self._say = say
        self._classify = classify
        self._build_node_tools = build_node_tools
        self._end_call = end_call
        self._transfer_call = transfer_call
        self._call_id = call_id

        self._current_node_id = config.start_node_id
        self._auto_transitions = 0
        self._ended = False
        # start_speaker == "user": the agent stays silent until the caller
        # speaks, so the start node's verbatim line waits for that turn
        # instead of being dropped.
        self._defer_speech = False
        self._pending_speech: str | None = None

        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            # ``subagent``'s field set is a strict subset of
            # ``conversation``'s, so the same handler is a faithful
            # degradation, not a stub. ``function`` and
            # ``extract_dynamic_variables`` also speak-or-listen and then
            # transition; their node-specific tool arrives through
            # `build_node_tools`, and the tool wrapper drives `advance`.
            "conversation": self._enter_conversation,
            "subagent": self._enter_conversation,
            "function": self._enter_conversation,
            "extract_dynamic_variables": self._enter_conversation,
            "branch": self._enter_branch,
            "end": self._enter_end,
            "transfer_call": self._enter_transfer_call,
        }

    # -- state ---------------------------------------------------------------

    @property
    def current_node_id(self) -> str:
        return self._current_node_id

    @property
    def ended(self) -> bool:
        """True once the flow hung up or handed the call off via transfer."""
        return self._ended

    # -- entry points --------------------------------------------------------

    async def start(self) -> None:
        """Enter the start node."""
        self._auto_transitions = 0
        node = self._graph.start
        self._defer_speech = self._config.start_speaker == "user"
        logger.info(
            "flow call=%s starting at node %s (%s)",
            self._call_id,
            node.get("id"),
            node.get("type"),
        )
        try:
            await self._enter(node)
        finally:
            self._defer_speech = False

    async def advance(self, edge: dict[str, Any]) -> None:
        """Follow *edge* to its destination and enter it.

        Called when something outside the runtime decided — the model's
        transition tool, a function node's tool result. That counts as a
        fresh turn, so the automatic-transition budget resets.
        """
        if self._ended:
            logger.debug("flow call=%s ignoring advance after the call ended", self._call_id)
            return
        self._auto_transitions = 0
        await self._follow(edge)

    async def on_user_turn(self) -> None:
        """Re-evaluate the current node's equation edges, then its always_edge."""
        if self._ended:
            return
        self._auto_transitions = 0
        if self._pending_speech is not None:
            pending, self._pending_speech = self._pending_speech, None
            await self._say(pending)
        node = self._current_node()
        if node is None:
            return
        edge = select_equation_edge(node, self._variables)
        if edge is not None:
            await self._auto_follow(edge)
            return
        always = node.get("always_edge")
        if isinstance(always, dict) and always.get("destination_node_id"):
            await self._auto_follow(always)

    # -- traversal -----------------------------------------------------------

    def _current_node(self) -> dict[str, Any] | None:
        try:
            return self._graph.node(self._current_node_id)
        except FlowError:
            logger.error(
                "flow call=%s current node %r is not in the graph",
                self._call_id,
                self._current_node_id,
            )
            return None

    async def _follow(self, edge: dict[str, Any]) -> None:
        destination = edge.get("destination_node_id") if isinstance(edge, dict) else None
        if not isinstance(destination, str) or not destination:
            logger.warning(
                "flow call=%s node %s: edge %r has no destination; staying put",
                self._call_id,
                self._current_node_id,
                (edge or {}).get("id") if isinstance(edge, dict) else edge,
            )
            return
        try:
            # Never `graph.node(edge["id"])`: a synthetic ``global::`` edge id
            # is not a node id.
            node = self._graph.node(destination)
        except FlowError:
            logger.error(
                "flow call=%s node %s: edge %r points at missing node %r",
                self._call_id,
                self._current_node_id,
                edge.get("id"),
                destination,
            )
            return
        logger.info(
            "flow transition call=%s %s -> %s via edge %s",
            self._call_id,
            self._current_node_id,
            destination,
            edge.get("id"),
        )
        await self._enter(node)

    async def _auto_follow(self, edge: dict[str, Any]) -> None:
        """Follow an edge the runtime chose itself (equation, branch, skip, always)."""
        if self._auto_transitions >= MAX_AUTOMATIC_TRANSITIONS:
            logger.error(
                "flow call=%s stopping at node %s: %d automatic transitions without a "
                "user turn (cycle of silent nodes?)",
                self._call_id,
                self._current_node_id,
                self._auto_transitions,
            )
            return
        self._auto_transitions += 1
        await self._follow(edge)

    async def _enter(self, node: dict[str, Any]) -> None:
        node_id = node.get("id")
        self._current_node_id = node_id if isinstance(node_id, str) else ""
        handler = self._handlers.get(node.get("type"))
        if handler is None:  # unreachable: FlowGraph rejects unknown types
            logger.error(
                "flow call=%s node %s has unsupported type %r",
                self._call_id,
                self._current_node_id,
                node.get("type"),
            )
            return
        await handler(node)

    # -- what the model is shown ---------------------------------------------

    def _model_view(self, node: dict[str, Any]) -> dict[str, Any]:
        """*node* minus the edges the model must never be offered.

        ``always_edge`` fires unconditionally on the next user turn and
        ``skip_response_edge`` is taken by the runtime, so neither belongs in
        the transition list or the transition tool's enum. Stripping them from
        a shallow copy keeps `prompt_edges` and `node_instructions`
        consistent with each other without teaching them about turn-taking.
        """
        if "always_edge" not in node and "skip_response_edge" not in node:
            return node
        view = dict(node)
        view.pop("always_edge", None)
        view.pop("skip_response_edge", None)
        return view

    async def _install(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        """Hand the model this node: instructions plus its tools."""
        view = self._model_view(node)
        edges = prompt_edges(view, self._graph.global_nodes)
        await self._set_instructions(node_instructions(view, self._graph, self._variables))
        await self._set_tools(self._build_node_tools(node, edges))
        return edges

    async def _speak_static(self, node: dict[str, Any]) -> bool:
        """Speak a ``static_text`` instruction verbatim. True if there was one."""
        line = static_text(node, self._variables)
        if not line:
            return False
        if self._defer_speech:
            self._pending_speech = line
            return True
        await self._say(line)
        return True

    # -- node handlers -------------------------------------------------------

    async def _enter_conversation(self, node: dict[str, Any]) -> None:
        """conversation / subagent / function / extract_dynamic_variables.

        A speaking node's entry is not a transition point: it must be allowed
        to deliver its line and collect a response first, otherwise a
        greeting could be skipped and the caller would hear dead air. Its
        equation edges are evaluated on the next user turn instead
        (`on_user_turn`) — *unless* the node carries a ``skip_response_edge``,
        in which case there is no next user turn to wait for.

        ``skip_response_edge`` means skip WAITING FOR the caller's response,
        not skip speaking: the node installs and speaks exactly like any
        other node (a ``static_text`` line via `_speak_static`, a ``prompt``
        instruction as a model turn request via `_install`), and only then —
        with no user turn in between — immediately follows the
        skip_response_edge, unless an equation edge fires first (equation
        edges take precedence at every transition point, this one included).
        A node whose own prompt edges give the model something to choose
        keeps its turn instead; auto-advancing would strand those edges.
        """
        await self._install(node)
        await self._speak_static(node)
        skip = node.get("skip_response_edge")
        if not (
            isinstance(skip, dict)
            and skip.get("destination_node_id")
            and not prompt_edges(self._model_view(node), [])
        ):
            return
        edge = select_equation_edge(node, self._variables)
        if edge is None:
            edge = skip
            logger.info(
                "flow call=%s node %s spoke, now takes its skip_response_edge "
                "without waiting for a user turn",
                self._call_id,
                self._current_node_id,
            )
        else:
            logger.info(
                "flow call=%s node %s spoke, but an equation edge fired ahead of "
                "its skip_response_edge",
                self._call_id,
                self._current_node_id,
            )
        await self._auto_follow(edge)

    async def _enter_branch(self, node: dict[str, Any]) -> None:
        """Pure routing: a branch node speaks nothing and installs nothing."""
        edge = select_equation_edge(node, self._variables)
        if edge is not None:
            await self._auto_follow(edge)
            return
        view = self._model_view(node)
        edges = prompt_edges(view, self._graph.global_nodes)
        if edges:
            chosen = await self._classify(
                node_instructions(view, self._graph, self._variables), edges
            )
            if chosen:
                for candidate in edges:
                    if candidate.get("id") == chosen:
                        await self._auto_follow(candidate)
                        return
                logger.warning(
                    "flow call=%s node %s: classifier named unknown edge %r",
                    self._call_id,
                    self._current_node_id,
                    chosen,
                )
        await self._follow_fallback(node)

    async def _enter_end(self, node: dict[str, Any]) -> None:
        if node.get("speak_during_execution") and not await self._speak_static(node):
            # A ``prompt`` instruction has to be phrased, which needs a model
            # turn the runtime cannot request itself; handing it to the
            # session as instructions is the closest faithful thing.
            await self._set_instructions(
                node_instructions(self._model_view(node), self._graph, self._variables)
            )
        self._ended = True
        logger.info("flow call=%s ending the call at node %s", self._call_id, self._current_node_id)
        await self._end_call(HANGUP_REASON)

    async def _enter_transfer_call(self, node: dict[str, Any]) -> None:
        if node.get("speak_during_execution"):
            await self._speak_static(node)
        number = self._transfer_number(node)
        if not number:
            await self._follow_fallback(node)
            return
        try:
            result = await self._transfer_call(number)
        except Exception:
            logger.warning(
                "flow call=%s node %s: transfer to %s raised",
                self._call_id,
                self._current_node_id,
                number,
                exc_info=True,
            )
            await self._follow_fallback(node)
            return
        if _transfer_failed(result):
            logger.warning(
                "flow call=%s node %s: transfer to %s failed: %s",
                self._call_id,
                self._current_node_id,
                number,
                result,
            )
            await self._follow_fallback(node)
            return
        # A cold transfer hands the caller off: this leg is over.
        self._ended = True
        logger.info(
            "flow call=%s node %s transferred the call to %s",
            self._call_id,
            self._current_node_id,
            number,
        )

    def _transfer_number(self, node: dict[str, Any]) -> str:
        """The E.164 number a ``transfer_call`` node should dial, or ``""``.

        ``transfer_destination.type`` is ``predefined`` (a ``number``) or
        ``inferred`` (a ``prompt`` describing where to send the call). There
        is no model turn available here, so an inferred destination is usable
        only when its prompt resolves to a concrete number — the common
        ``{{transfer_number}}`` case. Anything else falls through to the
        node's failure edge rather than dialing prose.
        """
        destination = node.get("transfer_destination")
        if not isinstance(destination, dict):
            destination = {}
        kind = destination.get("type")
        raw = destination.get("prompt") if kind == "inferred" else destination.get("number")
        if not isinstance(raw, str) or not raw:
            raw = destination.get("number") or destination.get("prompt") or node.get("number")
        if not isinstance(raw, str) or not raw:
            logger.warning(
                "flow call=%s node %s has no transfer destination",
                self._call_id,
                self._current_node_id,
            )
            return ""
        number = resolve_template(raw, self._variables).strip()
        if not node.get("ignore_e164_validation") and not _E164_RE.match(number):
            # The destination can be steered by untrusted caller speech, so a
            # non-E.164 value is never dialed (same guard as the built-in
            # transfer tool).
            logger.warning(
                "flow call=%s node %s: transfer destination %r (%s) is not E.164",
                self._call_id,
                self._current_node_id,
                number,
                kind,
            )
            return ""
        return number

    async def _follow_fallback(self, node: dict[str, Any]) -> None:
        """Take the node's guaranteed fallback (``else_edge``, else ``edge``)."""
        edge = fallback_edge(node)
        if edge is None:
            logger.error(
                "flow call=%s node %s has no transition to follow; staying put",
                self._call_id,
                self._current_node_id,
            )
            return
        await self._auto_follow(edge)
