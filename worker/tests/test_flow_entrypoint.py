"""`main.py`'s flow wiring. Requires the livekit stack — skipped in dev-only CI.

`main.py` imports livekit at module scope, so it cannot even be imported in the
worker's dev-only environment; the decisions that do not need livekit live in
`arhiteq_worker.flow` and are tested by `test_flow_wiring.py`. What is left
here is the part that genuinely needs the stack: turning those decisions into
real `function_tool`s, mapping the flow's model onto the Gemini catalogue, and
driving a real `FlowRuntime` through a fake session/agent.
"""

import asyncio

import httpx
import pytest

pytest.importorskip("livekit.agents")

from arhiteq_worker.config import CallConfig, ConversationFlowConfig
from arhiteq_worker.flow import FlowError, FlowGraph, prompt_edges
from arhiteq_worker.main import (
    DEFAULT_GEMINI_MODEL,
    _background_tasks,
    _cancel_background_tasks,
    _FlowWiring,
    _gemini_model,
    _one_shot_completion,
    _spawn,
    _wire_session_events,
)
from arhiteq_worker.state import CallState

GET_MEMBER = "node-1773865257897"  # function node with TWO usable success edges
WELCOME = "start-node-1773864713478"


def _run(coro):
    return asyncio.run(coro)


class _FakeAgent:
    def __init__(self) -> None:
        self.instructions: list[str] = []
        self.tools: list[list] = []

    async def update_instructions(self, instructions: str) -> None:
        self.instructions.append(instructions)

    async def update_tools(self, tools: list) -> None:
        self.tools.append(tools)


class _FakeSession:
    """Enough AgentSession for the wiring: a TTS marker and two speech seams."""

    def __init__(self, *, tts: bool = True) -> None:
        self.tts = object() if tts else None
        self.said: list[str] = []
        self.replies: list[str | None] = []

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def generate_reply(self, instructions: str | None = None) -> None:
        self.replies.append(instructions)


class _FakeControl:
    def __init__(self) -> None:
        self.ended: list[tuple[str, bool]] = []
        self.transferred: list[str] = []

    async def end_call(self, reason: str = "agent_hangup", *, flush_grace: bool = False) -> None:
        self.ended.append((reason, flush_grace))

    async def transfer_call(self, number: str) -> str:
        self.transferred.append(number)
        return '{"result": "ok"}'


class _FakeKnowledge:
    async def search_knowledge_base(self, call_id: str, query: str, **kwargs) -> dict:
        return {"results": []}


def _wiring(
    flow_dict: dict,
    *,
    variables: dict | None = None,
    state: CallState | None = None,
    control: _FakeControl | None = None,
) -> _FlowWiring:
    cfg = CallConfig.from_dict({"call_id": "call_1", "conversation_flow": flow_dict})
    assert cfg.conversation_flow is not None
    return _FlowWiring(
        cfg=cfg,
        flow=cfg.conversation_flow,
        control=control or _FakeControl(),
        state=state or CallState(call_id="call_1"),
        variables=variables if variables is not None else {},
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        knowledge=_FakeKnowledge(),
    )


def _graph(flow_dict: dict) -> FlowGraph:
    return FlowGraph.from_config(ConversationFlowConfig.from_dict(flow_dict))


def _names(tools: list) -> list[str]:
    return [tool.info.name for tool in tools]


def _handler(tool):
    return getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)


def _prompt_edge(edge_id: str, prompt: str, destination: str) -> dict:
    return {
        "id": edge_id,
        "transition_condition": {"type": "prompt", "prompt": prompt},
        "destination_node_id": destination,
    }


def _two_step_flow(**start_extra) -> dict:
    start = {
        "id": "n1",
        "type": "conversation",
        "name": "Greeting",
        "instruction": {"type": "static_text", "text": "Hi, this is Clara."},
        "edges": [_prompt_edge("e2", "Caller says yes", "n2")],
    }
    start.update(start_extra)
    return {
        "global_prompt": "Be brief.",
        "start_node_id": "n1",
        "start_speaker": "agent",
        "nodes": [
            start,
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Great, thanks."},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Graph validation: a bad flow aborts the call at start
# ---------------------------------------------------------------------------


def test_an_unsupported_node_type_is_rejected_before_the_call_starts() -> None:
    with pytest.raises(FlowError) as excinfo:
        _wiring(
            {
                "start_node_id": "n1",
                "nodes": [
                    {"id": "n1", "type": "conversation"},
                    {"id": "n2", "type": "component"},
                ],
            }
        )
    # The entrypoint logs this and shuts the job down; the node id has to be in
    # it or the operator cannot find the offending node.
    assert "n2" in str(excinfo.value)


def test_an_edge_into_nowhere_is_rejected_before_the_call_starts() -> None:
    with pytest.raises(FlowError) as excinfo:
        _wiring(
            {
                "start_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "conversation",
                        "edges": [_prompt_edge("e1", "Yes", "gone")],
                    }
                ],
            }
        )
    assert "gone" in str(excinfo.value)


def test_the_real_fixtures_all_load(prior_auth_flow) -> None:
    from conftest import load_retell_flow_fixture

    for name in ("clara_outbound.json", "identity_verify_transfer.json"):
        _wiring(load_retell_flow_fixture(name))
    _wiring(prior_auth_flow)


# ---------------------------------------------------------------------------
# Model mapping: an OpenAI model id never reaches a provider
# ---------------------------------------------------------------------------


def test_an_openai_flow_model_maps_onto_the_gemini_catalogue(prior_auth_flow) -> None:
    wiring = _wiring(prior_auth_flow)
    assert wiring.model == "gpt-5.1"  # what build_session is handed …
    assert _gemini_model(wiring.model) == DEFAULT_GEMINI_MODEL  # … and what it uses


def test_a_gemini_flow_model_is_used_as_named() -> None:
    from conftest import load_retell_flow_fixture

    wiring = _wiring(load_retell_flow_fixture("clara_outbound.json"))
    assert _gemini_model(wiring.model) == "gemini-3.1-flash-lite"
    assert wiring.temperature == 0.3


def test_a_flow_without_a_model_choice_leaves_the_defaults_alone() -> None:
    wiring = _wiring(_two_step_flow())
    assert wiring.model == ""  # build_session falls back to the agent's own
    assert wiring.temperature is None


# ---------------------------------------------------------------------------
# What the agent starts with
# ---------------------------------------------------------------------------


def test_the_agent_opens_on_the_start_nodes_instructions(prior_auth_flow) -> None:
    wiring = _wiring(prior_auth_flow)
    instructions = wiring.start_instructions
    # The flow's global prompt plus the start node's own transitions — not
    # llm.general_prompt, which a flow-backed agent does not have at all.
    assert "Working Hours" in instructions
    assert "Available transitions:" in instructions


def test_the_agent_opens_with_the_start_nodes_tools(prior_auth_flow) -> None:
    wiring = _wiring(prior_auth_flow)
    assert _names(wiring.start_tools()) == ["transition_to"]


def test_templates_in_the_start_node_resolve_against_the_live_variables() -> None:
    flow = _two_step_flow(instruction={"type": "prompt", "text": "Greet {{first_name}}."})
    wiring = _wiring(flow, variables={"first_name": "Ada"})
    assert "Greet Ada." in wiring.start_instructions


# ---------------------------------------------------------------------------
# build_node_tools
# ---------------------------------------------------------------------------


def test_multi_edge_function_node_gets_a_transition_tool_alongside_its_own(
    prior_auth_flow,
) -> None:
    """The review finding this wiring exists for.

    "Get Member" has two usable success edges, so `make_function_node_tool`
    deliberately does not auto-advance — the model must choose. If
    build_node_tools installed a transition tool only on plain conversation
    nodes, this node would stall with no way forward and the call would die.
    """
    wiring = _wiring(prior_auth_flow)
    graph = _graph(prior_auth_flow)
    node = graph.node(GET_MEMBER)
    tools = wiring.build_node_tools(node, prompt_edges(node, graph.global_nodes))
    names = _names(tools)
    assert "transition_to" in names
    assert len(names) == 2  # the transition tool and the node's own tool


def test_the_transition_tool_offers_every_edge_including_the_ambiguous_ones(
    prior_auth_flow,
) -> None:
    wiring = _wiring(prior_auth_flow)
    graph = _graph(prior_auth_flow)
    node = graph.node(GET_MEMBER)
    edges = prompt_edges(node, graph.global_nodes)
    tool = wiring.build_node_tools(node, edges)[0]
    enum = tool.info.raw_schema["parameters"]["properties"]["transition_to"]["enum"]
    assert enum == [edge["id"] for edge in edges]


def test_kb_lookup_is_attached_when_the_flow_carries_knowledge_bases() -> None:
    flow = _two_step_flow()
    flow["knowledge_base_ids"] = ["kb_1"]
    flow["kb_config"] = {"top_k": 3, "filter_score": 0.6}
    wiring = _wiring(flow)
    graph = _graph(flow)
    node = graph.start
    names = _names(wiring.build_node_tools(node, prompt_edges(node, [])))
    assert names == ["transition_to", "kb_lookup"]


def test_kb_lookup_is_absent_without_knowledge_bases() -> None:
    flow = _two_step_flow()
    wiring = _wiring(flow)
    graph = _graph(flow)
    node = graph.start
    assert _names(wiring.build_node_tools(node, prompt_edges(node, []))) == ["transition_to"]


# ---------------------------------------------------------------------------
# Driving a real FlowRuntime through the wiring
# ---------------------------------------------------------------------------


async def _settle() -> None:
    # `_transition` spawns the walk off the tool handler's stack (see
    # _FlowWiring's docstring), so let the spawned task run.
    for _ in range(10):
        await asyncio.sleep(0)


def test_start_speaks_the_start_nodes_line_and_installs_its_tools() -> None:
    async def scenario():
        wiring = _wiring(_two_step_flow())
        session, agent = _FakeSession(), _FakeAgent()
        wiring.attach(session, agent)
        await wiring.start()
        return session, agent

    session, agent = _run(scenario())
    assert session.said == ["Hi, this is Clara."]
    assert "Be brief." in agent.instructions[0]
    assert _names(agent.tools[0]) == ["transition_to"]


def test_a_start_node_start_speaker_override_defers_the_opening_line() -> None:
    async def scenario():
        # Flow says the agent opens; the start node overrides it to the caller.
        wiring = _wiring(_two_step_flow(start_speaker="user"))
        session, agent = _FakeSession(), _FakeAgent()
        wiring.attach(session, agent)
        await wiring.start()
        spoke_at_start = list(session.said)
        await wiring.on_user_turn()
        return spoke_at_start, session.said

    spoke_at_start, said = _run(scenario())
    assert spoke_at_start == []
    assert said == ["Hi, this is Clara."]


def test_the_model_choosing_a_transition_walks_the_graph() -> None:
    async def scenario():
        wiring = _wiring(_two_step_flow())
        session, agent = _FakeSession(), _FakeAgent()
        runtime = wiring.attach(session, agent)
        await wiring.start()
        tool = wiring.build_node_tools(
            _graph(_two_step_flow()).start, prompt_edges(_graph(_two_step_flow()).start, [])
        )[0]
        result = await _handler(tool)({"transition_to": "e2"}, None)
        await _settle()
        return runtime.current_node_id, session.said, result

    node_id, said, result = _run(scenario())
    assert node_id == "n2"
    assert said == ["Hi, this is Clara.", "Great, thanks."]
    assert result == "ok"


def test_an_edge_id_the_model_invented_is_reported_back_to_it() -> None:
    async def scenario():
        wiring = _wiring(_two_step_flow())
        session, agent = _FakeSession(), _FakeAgent()
        runtime = wiring.attach(session, agent)
        await wiring.start()
        graph = _graph(_two_step_flow())
        tool = wiring.build_node_tools(graph.start, prompt_edges(graph.start, []))[0]
        result = await _handler(tool)({"transition_to": "nope"}, None)
        await _settle()
        return runtime.current_node_id, result

    node_id, result = _run(scenario())
    assert node_id == "n1"  # stayed put
    assert result == "failed to transition"


def test_an_end_node_hangs_up_with_the_flush_grace_the_goodbye_needs() -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "end",
                "speak_during_execution": True,
                "instruction": {"type": "static_text", "text": "Goodbye."},
            }
        ],
    }

    async def scenario():
        control = _FakeControl()
        wiring = _wiring(flow, control=control)
        session, agent = _FakeSession(), _FakeAgent()
        wiring.attach(session, agent)
        await wiring.start()
        return control, session

    control, session = _run(scenario())
    assert session.said == ["Goodbye."]
    # flush_grace covers the room->SIP->phone tail so delete_room cannot clip
    # the line `say` just finished playing out.
    assert control.ended == [("agent_hangup", True)]


def test_a_transfer_node_dials_through_the_call_runtime() -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "transfer_call",
                "transfer_destination": {"type": "predefined", "number": "+15551234567"},
            }
        ],
    }

    async def scenario():
        control = _FakeControl()
        wiring = _wiring(flow, control=control)
        wiring.attach(_FakeSession(), _FakeAgent())
        await wiring.start()
        return control

    assert _run(scenario()).transferred == ["+15551234567"]


def test_a_speech_to_speech_session_voices_a_line_through_the_model() -> None:
    # Gemini Live has no TTS, so say() would raise; the line is voiced by the
    # realtime model instead (the same fallback ArhiteqAgent's greeting takes).
    async def scenario():
        wiring = _wiring(_two_step_flow())
        session = _FakeSession(tts=False)
        wiring.attach(session, _FakeAgent())
        await wiring.start()
        return session

    session = _run(scenario())
    assert session.said == []
    assert len(session.replies) == 1
    assert "Hi, this is Clara." in (session.replies[0] or "")


# ---------------------------------------------------------------------------
# The branch classifier
# ---------------------------------------------------------------------------


def test_a_classification_call_that_fails_answers_nothing() -> None:
    class _BrokenLLM:
        def chat(self, **kwargs):
            raise RuntimeError("no credentials")

    # Never raises: a branch that cannot be classified falls through to its
    # else_edge rather than taking the call down.
    assert _run(_one_shot_completion(_BrokenLLM(), "system", "user")) == ""


# ---------------------------------------------------------------------------
# FIX 3: spawned flow transitions are cancellable at teardown
# ---------------------------------------------------------------------------


def test_cancel_background_tasks_cancels_a_spawned_flow_transition() -> None:
    """`_finalize` must be able to stop an in-flight `_spawn`ed advance.

    `_spawn`ed work (a `_FlowWiring._transition` node walk, the initial
    `flow_wiring.start()`) previously outlived `_finalize`, since only the
    watchdog/pump `tasks` list was cancelled there -- an in-flight advance
    could keep running and `say`/`generate_reply` into an already-closed
    session. `_cancel_background_tasks` reuses the `_background_tasks` set
    `_spawn` already tracks every such task in.
    """

    async def scenario() -> asyncio.Task:
        never_settles = asyncio.get_event_loop().create_future()

        async def pretend_flow_transition() -> None:
            await never_settles

        task = _spawn(pretend_flow_transition())
        assert task in _background_tasks
        await asyncio.sleep(0)  # let it start awaiting
        assert not task.done()

        _cancel_background_tasks()
        await asyncio.sleep(0)  # let cancellation propagate
        return task

    task = _run(scenario())
    assert task.cancelled()


# ---------------------------------------------------------------------------
# FIX 4: the session-event wiring that actually calls on_user_turn
# ---------------------------------------------------------------------------


class _FakeItem:
    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.text_content = text


class _FakeItemEvent:
    def __init__(self, item: _FakeItem) -> None:
        self.item = item


class _EmitterSession(_FakeSession):
    """`_FakeSession` plus the `.on(event)` decorator wiring actually uses."""

    def __init__(self, *, tts: bool = True) -> None:
        super().__init__(tts=tts)
        self._handlers: dict[str, object] = {}

    def on(self, event: str):
        def decorator(fn):
            self._handlers[event] = fn
            return fn

        return decorator

    def fire(self, event: str, ev) -> None:
        handler = self._handlers.get(event)
        assert handler is not None, f"no handler registered for {event!r}"
        handler(ev)


class _FakeCallRuntime:
    async def end_call(self, reason: str = "agent_hangup", *, flush_grace: bool = False) -> None:
        pass


def test_conversation_item_added_drives_on_user_turn_once_per_committed_user_turn() -> None:
    """Nothing previously exercised `_wire_session_events`'s
    `conversation_item_added` handler itself -- `on_user_turn` was only ever
    called directly in tests. This drives the real handler through a fake
    session's event emitter and checks it fires `on_user_turn` exactly once
    per committed *user* turn, and not at all for agent turns.
    """

    calls: list[None] = []

    async def on_user_turn() -> None:
        calls.append(None)

    async def scenario() -> None:
        session = _EmitterSession()
        state = CallState(call_id="call_1")
        _wire_session_events(
            session,
            state,
            _FakeCallRuntime(),
            [],
            {"open": False},
            on_user_turn=on_user_turn,
        )

        session.fire("conversation_item_added", _FakeItemEvent(_FakeItem("assistant", "Hi there")))
        session.fire("conversation_item_added", _FakeItemEvent(_FakeItem("user", "Hello")))
        session.fire("conversation_item_added", _FakeItemEvent(_FakeItem("user", "Again")))
        await _settle()

        assert state.transcript_text()  # both roles were recorded either way

    _run(scenario())
    assert len(calls) == 2
