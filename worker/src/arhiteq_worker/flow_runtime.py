"""Walk a conversation-flow graph during a live call.

`arhiteq_worker.flow` is the pure decision layer (indexing, equations, edge
selection, prompt assembly). This module is the driver on top of it: it holds
"where the call is right now", enters nodes, and follows edges.

Like `flow`, it imports no ``livekit``: the worker's CI installs the dev-only
dependency group, so anything that touches the agent session — updating the
model's instructions, installing tools, speaking, requesting a model turn,
hanging up, transferring — arrives as an **injected callable**. `main.py`
(Task 7) supplies the real ones; tests supply recording fakes. If this module
ever needs to import ``livekit``, the seam is in the wrong place.

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
    is_global_edge,
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

# Reason recorded when a node that can only route has nowhere to route to
# (`_dead_end`). Not `HANGUP_REASON`: the agent did not decide to hang up, the
# graph ran out of usable edges — the same reason `main.py` records when a flow
# is rejected at load, so both graph problems read alike on the call record.
DEAD_END_REASON = "error_unknown"

# Same shape the built-in transfer tool enforces: a destination that reaches
# the SIP leg must be strict E.164 unless the node opts out.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

# Node types that exist only to ROUTE. They install no conversation of their
# own (a ``branch`` speaks nothing at all; a ``transfer_call`` has said
# whatever it was going to say before dialing) and cannot advance without an
# edge, so one of these with nowhere to go is not "staying put" — it is
# silence for the rest of the call, with the agent still holding the PREVIOUS
# node's instructions and tools. `_dead_end` ends the call for these.
# ``conversation``/``subagent`` are deliberately absent: they hold a live
# model turn, so a node of theirs with no usable edge keeps talking, which is
# a legitimate terminal-ish state rather than a dead end.
_ROUTING_NODE_TYPES: frozenset[str] = frozenset(
    {"branch", "function", "extract_dynamic_variables", "transfer_call"}
)

SetInstructions = Callable[[str], Awaitable[None]]
SetTools = Callable[[list[Any]], Awaitable[None]]
Say = Callable[[str], Awaitable[None]]
Classify = Callable[[str, list[dict[str, Any]]], Awaitable[str | None]]
BuildNodeTools = Callable[[dict[str, Any], list[dict[str, Any]]], list[Any]]
EndCall = Callable[[str], Awaitable[None]]
TransferCall = Callable[[str], Awaitable[str]]
# Passing instructions asks for a turn phrased by them; ``None`` asks for a
# turn from whatever instructions are already installed (`main.py`/Task 7
# wires this to ``session.generate_reply()`` / ``session.generate_reply(
# instructions=...)`` respectively — the same call the single-prompt
# greeting path in `main.py` already makes, so this seam's signature is kept
# compatible with it).
GenerateReply = Callable[[str | None], Awaitable[None]]


async def _default_generate_reply(_instructions: str | None) -> None:
    """No-op default so existing `FlowRuntime(...)` constructions keep working."""
    return


def _is_prompt_instruction(node: dict[str, Any]) -> bool:
    """Does *node* carry a ``prompt``-type instruction (needs a model turn to voice)?"""
    instruction = node.get("instruction")
    return isinstance(instruction, dict) and instruction.get("type") == "prompt"


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
        generate_reply: GenerateReply = _default_generate_reply,
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
        self._generate_reply = generate_reply
        self._call_id = call_id

        self._current_node_id = config.start_node_id
        self._auto_transitions = 0
        self._ended = False
        # Guards the start/first-user-turn race: `_FlowWiring` (main.py) wires
        # `on_user_turn` into the live session and calls `attach()` before
        # `session.start()`, but `start()` itself is only spawned afterward,
        # behind a couple of internal-API round trips. If the caller speaks
        # in that window, `on_user_turn` must not evaluate the start node's
        # equation/always edges -- it has never been entered (no instructions,
        # no tools installed) -- so it is a no-op until `start()` sets this.
        self._started = False
        # start_speaker == "user": the agent stays silent until the caller
        # speaks, so the start node's verbatim line waits for that turn
        # instead of being dropped.
        self._defer_speech = False
        # A list of thunks, not a single slot or a list of strings: an
        # auto-follow chain (skip_response_edge cascades) can park more than
        # one thing to do while `_defer_speech` is still true (it is only
        # reset in `start`'s ``finally``) -- a static line to `say`, a model
        # turn to `generate_reply` for a ``prompt`` instruction, or both, in
        # whichever order they were produced -- and each one must run, in
        # that order, once the first user turn arrives. Storing thunks
        # (rather than e.g. a tagged union) keeps the two speech paths
        # symmetric: neither is special-cased over the other, both are just
        # "a deferred thing to await".
        self._pending_speech: list[Callable[[], Awaitable[None]]] = []

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
        self._started = True
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

    async def advance(self, edge: dict[str, Any], *, from_node_id: str | None = None) -> None:
        """Follow *edge* to its destination and enter it.

        Called when something outside the runtime decided — the model's
        transition tool, a function node's tool result. That counts as a
        fresh turn, so the automatic-transition budget resets.

        *from_node_id*, when given, is the node the caller was on at the
        moment the transition was *requested* -- captured before the call was
        spawned off the handler's stack and queued behind the lock (see
        `main.py`'s `_transition`). If the runtime's cursor has since moved
        (a second `transition_to` requested from the same node while the
        first was still in flight, or a user-turn edge firing while a
        transition was already queued), this advance is stale: walking it
        from wherever the runtime now actually is would land the call two
        nodes deep off a single decision. Dropping it is a no-op, not an
        error -- the first, non-stale advance already did the real work.
        Omitting *from_node_id* (every call site that predates this guard)
        keeps the old, unguarded behaviour.
        """
        if self._ended:
            logger.debug("flow call=%s ignoring advance after the call ended", self._call_id)
            return
        if from_node_id is not None and from_node_id != self._current_node_id:
            logger.info(
                "flow call=%s dropping stale advance requested from %s: now at %s",
                self._call_id,
                from_node_id,
                self._current_node_id,
            )
            return
        self._auto_transitions = 0
        await self._follow(edge)

    async def on_user_turn(self, *, from_node_id: str | None = None) -> None:
        """Re-evaluate the current node's equation edges, then its always_edge.

        A no-op until `start()` has actually run (see `_started`): called
        before that, the "current node" has never been entered, so there are
        no edges of its own to evaluate yet -- doing so anyway is the
        start/first-user-turn race this guard closes.

        *from_node_id* is the same cursor guard `advance` carries, for the
        same reason. `main.py` spawns one of these per committed user item, so
        a caller who produces two turns while the agent is mid-``say`` (the
        lock is held) queues two of them: unguarded, the first takes N's
        ``always_edge`` to M and the second immediately takes M's, so M is
        entered and left without ever speaking to or hearing from the caller.
        The second turn was committed while the call was still on N, so it is
        stale and dropping it is a no-op, not an error. Omitting the argument
        (every call site that predates this guard, and the tests that drive
        turns one at a time) keeps the old, unguarded behaviour.
        """
        if not self._started or self._ended:
            return
        if from_node_id is not None and from_node_id != self._current_node_id:
            logger.info(
                "flow call=%s dropping a stale user turn from %s: now at %s",
                self._call_id,
                from_node_id,
                self._current_node_id,
            )
            return
        self._auto_transitions = 0
        await self._flush_pending_speech()
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

    # -- injected-callable containment ---------------------------------------

    async def _safely(self, what: str, action: Callable[..., Awaitable[Any]], *args: Any) -> bool:
        """Await one injected callable, containing a failure to this one step.

        Every side effect the runtime has is somebody else's callable (`say`,
        `set_tools`, `set_instructions`, `generate_reply` — see the module
        docstring). A raising one used to propagate straight out of `_enter`,
        killing the spawned task that was walking the graph and leaving the
        call on a HALF-entered node: instructions swapped, tools not, and
        nothing left running to notice. Degrading instead — log the failure
        against the node id and carry on with the rest of the entry — is
        strictly better: the worst case becomes one missing line or one stale
        tool set, not a stranded call.

        Returns whether the callable succeeded, for callers that care.
        """
        try:
            await action(*args)
            return True
        except Exception:
            logger.exception(
                "flow call=%s node %s: %s failed",
                self._call_id,
                self._current_node_id,
                what,
            )
            return False

    async def _flush_pending_speech(self) -> None:
        """Run everything `_defer_speech` parked, in the order it was parked.

        Called on the first user turn (the deferral's normal release) and by
        `_enter_end` on the way out: a line parked by a ``start_speaker:
        "user"`` start node whose ``skip_response_edge`` chain reaches an
        ``end`` node has no user turn left to release it, and `_speak_static`
        already reported it as spoken, so without this the closing line is
        silently dropped and the call just hangs up.
        """
        if not self._pending_speech:
            return
        pending, self._pending_speech = self._pending_speech, []
        for action in pending:
            await action()

    async def _clear_tools(self) -> None:
        """Drop the installed tools before asking for a closing model turn.

        `_enter_end` / `_enter_transfer_call` request a turn to phrase a
        ``prompt`` instruction while the PREVIOUS node's tools are still
        installed — so the model could call that stale ``transition_to``
        instead of speaking the closing line, and the advance it spawns would
        then race the hang-up/transfer immediately below. Neither node type
        offers the model any tool of its own, so clearing is the whole fix.
        """
        await self._safely("set_tools", self._set_tools, [])

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
        # Contained, not awaited bare: a failing `set_instructions` must not
        # abort the entry before the tools are installed (see `_safely`).
        await self._safely(
            "set_instructions",
            self._set_instructions,
            node_instructions(view, self._graph, self._variables),
        )
        try:
            tools = self._build_node_tools(node, edges)
        except Exception:
            # Also injected (it constructs livekit tools), so also contained:
            # a node with no tools still talks, a half-entered node does not.
            logger.exception(
                "flow call=%s node %s: build_node_tools failed",
                self._call_id,
                self._current_node_id,
            )
            tools = []
        await self._safely("set_tools", self._set_tools, tools)
        return edges

    async def _speak_static(self, node: dict[str, Any]) -> bool:
        """Speak a ``static_text`` instruction verbatim. True if there was one.

        A ``prompt`` instruction is not handled here at all — phrasing one
        needs a model turn, which is `_request_model_turn`'s job, not this
        method's; callers that must voice a prompt instruction with no user
        turn to trigger one naturally call `_request_model_turn` themselves.
        """
        line = static_text(node, self._variables)
        if not line:
            return False
        if self._defer_speech:
            self._pending_speech.append(lambda line=line: self._safely("say", self._say, line))
            return True
        await self._safely("say", self._say, line)
        return True

    async def _request_model_turn(self, instructions: str | None) -> None:
        """Ask for a model turn to phrase a ``prompt`` instruction, deferring like `_speak_static`.

        Symmetric with `_speak_static`: when `_defer_speech` is set (a
        ``start_speaker: "user"`` node must not have the agent open the
        conversation), the turn is parked in `_pending_speech` instead of
        requested immediately, so it lands only once the caller's first turn
        releases the deferral -- in the same relative order as any static
        line parked alongside it.
        """
        if self._defer_speech:
            self._pending_speech.append(
                lambda instructions=instructions: self._safely(
                    "generate_reply", self._generate_reply, instructions
                )
            )
            return
        await self._safely("generate_reply", self._generate_reply, instructions)

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
        other node — a ``static_text`` line via `_speak_static`, or, when
        that line is a ``prompt`` instead, by requesting a model turn via
        `_request_model_turn` (there is no next user turn to trigger one
        naturally here) — and only then, with no user turn in between,
        immediately follows the skip_response_edge, unless an equation edge
        fires first (equation edges take precedence at every transition
        point, this one included). A node whose own *authored* prompt edges
        give the model something to choose keeps its turn instead;
        auto-advancing would strand that choice.

        Synthetic ``global::...`` edges (one per global node, see
        `prompt_edges`) are deliberately excluded from that check. A global
        node is by definition reachable from anywhere with no authored edge —
        it is an escape hatch ("caller wants a human"), not a decision this
        node is waiting on, and the destination node offers the very same
        global edge right back, so the caller can still reach it on the next
        turn. Counting synthetic edges here would mean any flow with a global
        node (e.g. ``backend/tests/fixtures/retell_flows/prior_auth_hotline.json``,
        whose ``branch`` node is itself global) can never auto-advance any
        node at all, since every node is offered that node's synthetic edge.
        This has been re-derived twice now — don't re-derive it a third time.
        """
        edges = await self._install(node)
        authored_edges = [edge for edge in edges if not is_global_edge(edge)]
        spoken = await self._speak_static(node)
        skip = node.get("skip_response_edge")
        if not (isinstance(skip, dict) and skip.get("destination_node_id") and not authored_edges):
            return
        if not spoken and _is_prompt_instruction(node):
            # The line has to be phrased and nothing else will trigger a
            # model turn before the auto-follow below — ask for one now,
            # from the instructions `_install` just set.
            await self._request_model_turn(None)
        edge = select_equation_edge(node, self._variables, exclude_edge=skip)
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
        if (
            node.get("speak_during_execution")
            and not await self._speak_static(node)
            and _is_prompt_instruction(node)
        ):
            # A ``prompt`` instruction has to be phrased, and hanging up
            # right below is the last thing this node does — request a
            # model turn now, or the line is lost entirely. Drop the previous
            # node's tools first, so the model voices the closing line instead
            # of calling that node's stale ``transition_to``.
            await self._clear_tools()
            await self._request_model_turn(
                node_instructions(self._model_view(node), self._graph, self._variables)
            )
        # Anything a ``start_speaker: "user"`` deferral parked has no user turn
        # left to release it — this is the last chance to voice it.
        await self._flush_pending_speech()
        self._ended = True
        logger.info("flow call=%s ending the call at node %s", self._call_id, self._current_node_id)
        await self._end_call(HANGUP_REASON)

    async def _enter_transfer_call(self, node: dict[str, Any]) -> None:
        if node.get("speak_during_execution"):
            spoken = await self._speak_static(node)
            if not spoken and _is_prompt_instruction(node):
                # A ``prompt`` instruction has to be phrased, and a
                # successful transfer ends this leg right below — request a
                # model turn now, before dialing, or the line is lost
                # entirely (same shape as the ``end``-node fix above), with
                # the previous node's tools dropped first so the model cannot
                # call a stale ``transition_to`` in place of the line.
                await self._clear_tools()
                await self._request_model_turn(
                    node_instructions(self._model_view(node), self._graph, self._variables)
                )
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
            await self._dead_end(node)
            return
        await self._auto_follow(edge)

    async def _dead_end(self, node: dict[str, Any]) -> None:
        """*node* has no usable transition left. End the call, or stay put.

        A dangling fallback edge (authored with no ``destination_node_id``)
        cannot be caught by `FlowGraph.from_config` — there is no destination
        to validate against — and the real
        ``backend/tests/fixtures/retell_flows/prior_auth_hotline.json`` has two
        of them, so this state is reachable on a real flow and the graph must
        keep loading. What must not happen is what used to: log "staying put"
        and return, leaving the agent on the PREVIOUS node's instructions and
        tools with ``_ended`` false, so the caller hears nothing at all until
        the inactivity watchdog fires. That is exactly the transfer-failure
        trace on that fixture — `CallRuntime.transfer_call` errors on every
        non-SIP (i.e. every web) call, the failure edge is dangling, and the
        call goes silent.

        So: a node that can hold a conversation stays put (the model keeps
        talking — legitimate). A node that can only route
        (`_ROUTING_NODE_TYPES`) ends the call instead of stalling it.
        """
        node_type = node.get("type")
        if node_type not in _ROUTING_NODE_TYPES:
            logger.error(
                "flow call=%s node %s (%s) has no transition to follow; staying put",
                self._call_id,
                self._current_node_id,
                node_type,
            )
            return
        logger.error(
            "flow call=%s node %s (%s) has no usable transition and cannot hold the "
            "conversation; ending the call instead of stalling it",
            self._call_id,
            self._current_node_id,
            node_type,
        )
        self._ended = True
        await self._end_call(DEAD_END_REASON)
