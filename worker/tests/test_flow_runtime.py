"""Driving a conversation flow through a live call (no livekit stack).

`FlowRuntime` is the piece that walks the graph during a call, so every
side-effecting capability it needs is an injected callable. These tests pass
plain recording fakes and assert on what was recorded — nothing here imports
``livekit``, which is what keeps the worker's dev-only CI able to run them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from arhiteq_worker.config import CallConfig, ConversationFlowConfig
from arhiteq_worker.flow import FlowGraph
from arhiteq_worker.flow_runtime import (
    _ROUTING_NODE_TYPES,
    DEAD_END_REASON,
    MAX_AUTOMATIC_TRANSITIONS,
    FlowRuntime,
)

FLOW_LOGGER = "arhiteq-worker.flow"


def _run(coro) -> Any:
    return asyncio.run(coro)


def _ids(edges: list[dict[str, Any]]) -> list[str]:
    return [edge.get("id") for edge in edges]


class Fakes:
    """Recording stand-ins for every injected callable."""

    def __init__(
        self,
        *,
        classify_results: list[str | None] | None = None,
        transfer_result: str | Exception = '{"result": "call transferred to +15555550101"}',
    ) -> None:
        self.instructions: list[str] = []
        self.tools: list[list[Any]] = []
        self.said: list[str] = []
        self.classify_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.build_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.ended: list[str] = []
        self.transfers: list[str] = []
        self.generate_reply_calls: list[str | None] = []
        self._classify_results = list(classify_results or [])
        self.transfer_result = transfer_result

    async def set_instructions(self, text: str) -> None:
        self.instructions.append(text)

    async def set_tools(self, tools: list[Any]) -> None:
        self.tools.append(tools)

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        self.said.append(text)

    async def generate_reply(self, instructions: str | None) -> None:
        self.generate_reply_calls.append(instructions)

    async def classify(self, prompt: str, edges: list[dict[str, Any]]) -> str | None:
        self.classify_calls.append((prompt, list(edges)))
        return self._classify_results.pop(0) if self._classify_results else None

    def build_node_tools(self, node: dict[str, Any], edges: list[dict[str, Any]]) -> list[Any]:
        self.build_calls.append((node.get("id"), list(edges)))
        return [f"transition_to@{node.get('id')}"]

    async def end_call(self, reason: str) -> None:
        self.ended.append(reason)

    async def transfer_call(self, number: str) -> str:
        self.transfers.append(number)
        if isinstance(self.transfer_result, Exception):
            raise self.transfer_result
        return self.transfer_result

    # -- convenience readers -------------------------------------------------

    @property
    def offered_edges(self) -> list[dict[str, Any]]:
        """Edges offered to the model at the most recent node."""
        return self.build_calls[-1][1] if self.build_calls else []


def _runtime(
    flow_dict: dict[str, Any],
    fakes: Fakes,
    *,
    variables: dict[str, Any] | None = None,
    call_id: str = "call_abc",
) -> FlowRuntime:
    config = ConversationFlowConfig.from_dict(flow_dict)
    graph = FlowGraph.from_config(config)
    return FlowRuntime(
        graph,
        config,
        variables if variables is not None else {},
        set_instructions=fakes.set_instructions,
        set_tools=fakes.set_tools,
        say=fakes.say,
        classify=fakes.classify,
        build_node_tools=fakes.build_node_tools,
        end_call=fakes.end_call,
        transfer_call=fakes.transfer_call,
        generate_reply=fakes.generate_reply,
        call_id=call_id,
    )


def _prompt_condition(text: str) -> dict[str, Any]:
    return {"type": "prompt", "prompt": text}


def _equation_condition(left: Any, operator: str, right: Any) -> dict[str, Any]:
    return {
        "type": "equation",
        "operator": "&&",
        "equations": [{"left": left, "operator": operator, "right": right}],
    }


# ---------------------------------------------------------------------------
# conversation / subagent
# ---------------------------------------------------------------------------


def test_entering_a_conversation_node_sets_instructions_and_installs_tools() -> None:
    flow = {
        "global_prompt": "You work for {{company}}.",
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Ask for the medication name."},
                "edges": [
                    {
                        "id": "p1",
                        "transition_condition": _prompt_condition("Name given"),
                        "destination_node_id": "n2",
                    }
                ],
            },
            {"id": "n2", "type": "end"},
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes, variables={"company": "Acme"})
    _run(runtime.start())

    assert runtime.current_node_id == "n1"
    assert len(fakes.instructions) == 1
    assert "Ask for the medication name." in fakes.instructions[0]
    assert "You work for Acme." in fakes.instructions[0]
    # The transition tool is built from this node's prompt edges and installed.
    assert fakes.build_calls == [("n1", [flow["nodes"][0]["edges"][0]])]
    assert fakes.tools == [["transition_to@n1"]]
    assert fakes.said == []


def test_a_subagent_node_behaves_exactly_like_a_conversation_node() -> None:
    def _flow(node_type: str) -> dict[str, Any]:
        return {
            "global_prompt": "Global.",
            "start_node_id": "n1",
            "nodes": [
                {
                    "id": "n1",
                    "type": node_type,
                    "instruction": {"type": "prompt", "text": "Collect the member details."},
                    "edges": [
                        {
                            "id": "p1",
                            "transition_condition": _prompt_condition("Details given"),
                            "destination_node_id": "n2",
                        }
                    ],
                },
                {"id": "n2", "type": "end"},
            ],
        }

    conversation_fakes = Fakes()
    _run(_runtime(_flow("conversation"), conversation_fakes).start())
    subagent_fakes = Fakes()
    _run(_runtime(_flow("subagent"), subagent_fakes).start())

    assert subagent_fakes.instructions == conversation_fakes.instructions
    assert subagent_fakes.tools == conversation_fakes.tools
    assert _ids(subagent_fakes.offered_edges) == _ids(conversation_fakes.offered_edges)
    assert subagent_fakes.said == conversation_fakes.said == []


def test_a_static_text_node_speaks_the_line_verbatim() -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Please hold, {{first_name}}."},
            }
        ],
    }
    fakes = Fakes()
    _run(_runtime(flow, fakes, variables={"first_name": "Jo"}).start())

    assert fakes.said == ["Please hold, Jo."]
    # The model is never asked to phrase a static line.
    assert all("Please hold" not in text for text in fakes.instructions)


# ---------------------------------------------------------------------------
# end
# ---------------------------------------------------------------------------


def test_an_end_node_speaks_its_instruction_first_then_ends_the_call() -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "end",
                "speak_during_execution": True,
                "instruction": {"type": "static_text", "text": "Thanks for calling, goodbye!"},
            }
        ],
    }
    fakes = Fakes()
    _run(_runtime(flow, fakes).start())

    assert fakes.said == ["Thanks for calling, goodbye!"]
    assert fakes.ended == ["agent_hangup"]
    # A static_text line is spoken verbatim, never handed to a model turn.
    assert fakes.generate_reply_calls == []


def test_an_end_node_without_speak_during_execution_says_nothing() -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "end",
                "speak_during_execution": False,
                "instruction": {"type": "static_text", "text": "Thanks for calling, goodbye!"},
            }
        ],
    }
    fakes = Fakes()
    _run(_runtime(flow, fakes).start())

    assert fakes.said == []
    assert fakes.ended == ["agent_hangup"]


def test_an_end_node_with_a_prompt_instruction_requests_a_model_turn_before_hanging_up() -> None:
    """No say(): a prompt instruction has to be phrased, so a model turn is requested.

    Nothing else would ever trigger that turn -- `end_call` follows right
    after -- so without this the line is simply lost (dead air, then hangup).
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "end",
                "speak_during_execution": True,
                "instruction": {"type": "prompt", "text": "Politely end the call"},
            }
        ],
    }
    fakes = Fakes()
    _run(_runtime(flow, fakes).start())

    assert fakes.said == []
    assert len(fakes.generate_reply_calls) == 1
    assert "Politely end the call" in (fakes.generate_reply_calls[0] or "")
    assert fakes.ended == ["agent_hangup"]


def test_an_end_node_with_no_speak_flag_at_all_still_says_its_line() -> None:
    """Absent ``speak_during_execution`` is not false -- it is "never toggled".

    Every end/transfer node in the real
    ``backend/tests/fixtures/retell_flows/identity_verify_transfer.json``
    export is this shape: a filled-in instruction and no flag. Reading absent
    as false hung up mid-conversation without a closing word.
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "end",
                "instruction": {"type": "prompt", "text": "Politely end the call"},
            }
        ],
    }
    fakes = Fakes()
    _run(_runtime(flow, fakes).start())

    assert len(fakes.generate_reply_calls) == 1
    assert "Politely end the call" in (fakes.generate_reply_calls[0] or "")
    assert fakes.ended == ["agent_hangup"]


def test_an_end_node_with_no_speak_flag_and_no_instruction_stays_silent() -> None:
    """ "Speak the line you were given" needs a line; nothing to voice is still silence."""
    flow = {"start_node_id": "n1", "nodes": [{"id": "n1", "type": "end"}]}
    fakes = Fakes()
    _run(_runtime(flow, fakes).start())

    assert fakes.said == []
    assert fakes.generate_reply_calls == []
    assert fakes.ended == ["agent_hangup"]


# ---------------------------------------------------------------------------
# transfer_call
# ---------------------------------------------------------------------------


def _transfer_flow(destination: dict[str, Any], **node_extra: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": "t1",
        "type": "transfer_call",
        "transfer_destination": destination,
        "edge": {
            "id": "transfer-failed",
            "transition_condition": _prompt_condition("Transfer failed"),
            "destination_node_id": "n2",
        },
    }
    node.update(node_extra)
    return {
        "start_node_id": "t1",
        "nodes": [
            node,
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Sorry, I could not transfer you."},
            },
        ],
    }


def test_a_transfer_call_node_transfers_to_a_predefined_number() -> None:
    fakes = Fakes()
    runtime = _runtime(_transfer_flow({"type": "predefined", "number": "+15555550101"}), fakes)
    _run(runtime.start())

    assert fakes.transfers == ["+15555550101"]
    # Success: the failure edge is not followed.
    assert runtime.current_node_id == "t1"
    assert fakes.said == []


def test_a_transfer_node_with_no_speak_flag_warns_the_caller_before_dialing() -> None:
    """The `identity_verify_transfer.json` shape: an instruction, no flag.

    Reading absent as false cold-transferred the caller with no warning; the
    line has to be voiced (and, being a ``prompt``, phrased by a model turn)
    before the leg is handed off.
    """
    fakes = Fakes()
    runtime = _runtime(
        _transfer_flow(
            {"type": "predefined", "number": "+15555550101"},
            instruction={"type": "prompt", "text": "Transferring your call now."},
        ),
        fakes,
    )
    _run(runtime.start())

    assert len(fakes.generate_reply_calls) == 1
    assert "Transferring your call now." in (fakes.generate_reply_calls[0] or "")
    assert fakes.transfers == ["+15555550101"]


def test_a_failed_transfer_follows_the_nodes_single_edge() -> None:
    fakes = Fakes(transfer_result=json.dumps({"error": "transfer not supported on this call"}))
    runtime = _runtime(_transfer_flow({"type": "predefined", "number": "+15555550101"}), fakes)
    _run(runtime.start())

    assert fakes.transfers == ["+15555550101"]
    assert runtime.current_node_id == "n2"
    assert fakes.said == ["Sorry, I could not transfer you."]


def test_a_raising_transfer_follows_the_nodes_single_edge() -> None:
    fakes = Fakes(transfer_result=RuntimeError("SIP REFER failed"))
    runtime = _runtime(_transfer_flow({"type": "predefined", "number": "+15555550101"}), fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "n2"


def test_an_inferred_transfer_destination_resolves_a_variable_to_a_number() -> None:
    fakes = Fakes()
    runtime = _runtime(
        _transfer_flow({"type": "inferred", "prompt": "{{escalation_number}}"}),
        fakes,
        variables={"escalation_number": "+15555550199"},
    )
    _run(runtime.start())

    assert fakes.transfers == ["+15555550199"]


def test_an_inferred_transfer_destination_that_is_not_a_number_fails_over() -> None:
    """Without a model turn there is no way to infer a number from prose."""
    fakes = Fakes()
    runtime = _runtime(
        _transfer_flow({"type": "inferred", "prompt": "the on-call pharmacist"}), fakes
    )
    _run(runtime.start())

    assert fakes.transfers == []
    assert runtime.current_node_id == "n2"


def test_the_flow_transfer_guard_is_the_built_in_tools_guard() -> None:
    """Not "the same shape" — the same object, so the two cannot drift.

    A `\\d{7,14}` copy of this once lived in `flow_runtime` under a comment
    claiming parity with the built-in transfer tool, silently rejecting short
    national-format destinations `tools` dials fine.
    """
    from arhiteq_worker import flow_runtime, tools

    assert flow_runtime.E164_RE is tools.E164_RE


def test_a_short_national_format_destination_is_dialed_like_the_built_in_tool() -> None:
    """The regression the drifted copy caused: `+6831234` is valid E.164.

    Seven digits clears `tools.E164_RE` (`\\d{1,14}` after the leading digit)
    and the built-in transfer tool dials it. The old flow-local `\\d{7,14}`
    copy required eight and failed the node over to its failure edge instead.
    """
    fakes = Fakes()
    runtime = _runtime(_transfer_flow({"type": "predefined", "number": "+6831234"}), fakes)
    _run(runtime.start())

    assert fakes.transfers == ["+6831234"]
    assert runtime.ended


def test_ignore_e164_validation_does_not_unlock_a_non_e164_destination() -> None:
    """The node flag is parsed and deliberately not acted on.

    It could only ever widen what reaches the dialer:
    `CallRuntime.transfer_call` emits ``tel:{number}`` and nothing else, so a
    non-E.164 value addresses nothing — it just builds a malformed URI. The
    built-in transfer tool has no such opt-out, and neither does this path.
    """
    fakes = Fakes()
    runtime = _runtime(
        _transfer_flow(
            {"type": "predefined", "number": "not-a-number"},
            ignore_e164_validation=True,
        ),
        fakes,
    )
    _run(runtime.start())

    assert fakes.transfers == []
    assert runtime.current_node_id == "n2"  # failed over, exactly as without the flag


def test_ignore_e164_validation_does_not_dial_caller_steerable_prose() -> None:
    """The reason the flag is not honoured, stated as a test.

    An ``inferred`` destination resolves a ``{{var}}`` the model may have
    extracted from caller speech. With the flag honoured, a caller who gets
    ``escalation_number`` set to a premium-rate string would have it dialed.
    """
    fakes = Fakes()
    runtime = _runtime(
        _transfer_flow(
            {"type": "inferred", "prompt": "{{escalation_number}}"},
            ignore_e164_validation=True,
        ),
        fakes,
        variables={"escalation_number": "900-PREMIUM-RATE"},
    )
    _run(runtime.start())

    assert fakes.transfers == []
    assert runtime.current_node_id == "n2"


def test_a_transfer_node_speaks_its_line_when_speak_during_execution_is_true() -> None:
    fakes = Fakes()
    runtime = _runtime(
        _transfer_flow(
            {"type": "predefined", "number": "+15555550101"},
            speak_during_execution=True,
            instruction={"type": "static_text", "text": "Please stay on the line."},
        ),
        fakes,
    )
    _run(runtime.start())

    assert fakes.said == ["Please stay on the line."]
    assert fakes.transfers == ["+15555550101"]
    # A static_text line is said verbatim; no model turn is requested for it.
    assert fakes.generate_reply_calls == []


def test_a_transfer_node_requests_a_model_turn_for_a_prompt_instruction_before_transferring() -> (
    None
):
    """The same shape as the ``end``-node bug: a successful transfer sets
    ``_ended`` and hands the leg off, so nothing would ever trigger a model
    turn after the fact. A ``prompt`` instruction with
    ``speak_during_execution: true`` must be requested BEFORE the transfer is
    attempted, or the line is silently lost and the call transfers in
    silence.
    """
    events: list[str] = []
    fakes = Fakes()
    original_generate_reply = fakes.generate_reply
    original_transfer_call = fakes.transfer_call

    async def generate_reply(instructions: str | None) -> None:
        events.append("generate_reply")
        await original_generate_reply(instructions)

    async def transfer_call(number: str) -> str:
        events.append("transfer_call")
        return await original_transfer_call(number)

    fakes.generate_reply = generate_reply  # type: ignore[method-assign]
    fakes.transfer_call = transfer_call  # type: ignore[method-assign]

    runtime = _runtime(
        _transfer_flow(
            {"type": "predefined", "number": "+15555550101"},
            speak_during_execution=True,
            instruction={
                "type": "prompt",
                "text": "Tell them you're connecting them to a specialist",
            },
        ),
        fakes,
    )
    _run(runtime.start())

    # No say() for the prompt line -- it was requested as a model turn instead.
    assert fakes.said == []
    assert len(fakes.generate_reply_calls) == 1
    assert "connecting them to a specialist" in (fakes.generate_reply_calls[0] or "")
    assert fakes.transfers == ["+15555550101"]
    # The model turn happened strictly before the transfer was attempted.
    assert events == ["generate_reply", "transfer_call"]


# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------


def _branch_flow(branch: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_node_id": "b1",
        "nodes": [
            branch,
            {
                "id": "yes",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Yes branch."},
            },
            {
                "id": "no",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "No branch."},
            },
            {
                "id": "fallback",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Fallback branch."},
            },
        ],
    }


def test_a_branch_with_only_equation_edges_routes_with_zero_classify_calls() -> None:
    branch = {
        "id": "b1",
        "type": "branch",
        "edges": [
            {
                "id": "eq-no",
                "transition_condition": _equation_condition("{{age}}", ">", 100),
                "destination_node_id": "no",
            },
            {
                "id": "eq-yes",
                "transition_condition": _equation_condition("{{age}}", ">", 18),
                "destination_node_id": "yes",
            },
        ],
        "else_edge": {
            "id": "else",
            "transition_condition": _prompt_condition("Anything else"),
            "destination_node_id": "fallback",
        },
    }
    fakes = Fakes()
    runtime = _runtime(_branch_flow(branch), fakes, variables={"age": "42"})
    _run(runtime.start())

    assert fakes.classify_calls == []
    assert runtime.current_node_id == "yes"
    assert fakes.said == ["Yes branch."]
    # A branch node speaks nothing and installs nothing of its own.
    assert _ids(fakes.offered_edges) == []
    assert [node_id for node_id, _edges in fakes.build_calls] == ["yes"]


def test_a_branch_with_prompt_edges_calls_classify_once_and_follows_its_choice() -> None:
    branch = {
        "id": "b1",
        "type": "branch",
        "name": "Working Hour Split",
        "edges": [
            {
                "id": "p-no",
                "transition_condition": _prompt_condition("Outside working hours"),
                "destination_node_id": "no",
            },
            {
                "id": "p-yes",
                "transition_condition": _prompt_condition("During working hours"),
                "destination_node_id": "yes",
            },
        ],
        "else_edge": {
            "id": "else",
            "transition_condition": _prompt_condition("Anything else"),
            "destination_node_id": "fallback",
        },
    }
    fakes = Fakes(classify_results=["p-yes"])
    runtime = _runtime(_branch_flow(branch), fakes)
    _run(runtime.start())

    assert len(fakes.classify_calls) == 1
    prompt, edges = fakes.classify_calls[0]
    assert "Outside working hours" in prompt
    # The else edge carries a prompt condition too, so the model may name it
    # explicitly; when it names nothing we fall back to it anyway.
    assert _ids(edges) == ["p-no", "p-yes", "else"]
    assert runtime.current_node_id == "yes"


def test_a_branch_whose_classify_finds_no_match_follows_the_else_edge() -> None:
    branch = {
        "id": "b1",
        "type": "branch",
        "edges": [
            {
                "id": "p-yes",
                "transition_condition": _prompt_condition("During working hours"),
                "destination_node_id": "yes",
            }
        ],
        "else_edge": {
            "id": "else",
            "transition_condition": _prompt_condition("Anything else"),
            "destination_node_id": "fallback",
        },
    }
    fakes = Fakes(classify_results=[None])
    runtime = _runtime(_branch_flow(branch), fakes)
    _run(runtime.start())

    assert len(fakes.classify_calls) == 1
    assert runtime.current_node_id == "fallback"
    assert fakes.said == ["Fallback branch."]


def test_a_branch_whose_classify_names_an_unknown_edge_follows_the_else_edge() -> None:
    branch = {
        "id": "b1",
        "type": "branch",
        "edges": [
            {
                "id": "p-yes",
                "transition_condition": _prompt_condition("During working hours"),
                "destination_node_id": "yes",
            }
        ],
        "else_edge": {
            "id": "else",
            "transition_condition": _prompt_condition("Anything else"),
            "destination_node_id": "fallback",
        },
    }
    fakes = Fakes(classify_results=["made-up-edge"])
    runtime = _runtime(_branch_flow(branch), fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "fallback"


def test_a_branch_with_no_transition_at_all_ends_the_call_instead_of_stalling(caplog) -> None:
    """A routing node with no usable edge must END the call, never stay put.

    This test used to pin the opposite ("stays put and logs an error"), which
    enshrined the bug: a ``branch`` node speaks nothing and installs nothing,
    so a branch that cannot route leaves the agent holding the PREVIOUS node's
    instructions and tools with `_ended` still false — the caller hears
    silence until the inactivity watchdog fires minutes later. Staying put is
    only legitimate for a node that can hold a conversation
    (``conversation``/``subagent``): the model keeps talking there. Do not
    revert this to the stall.
    """
    branch = {"id": "b1", "type": "branch"}
    fakes = Fakes()
    runtime = _runtime(_branch_flow(branch), fakes)
    with caplog.at_level(logging.ERROR, logger=FLOW_LOGGER):
        _run(runtime.start())

    assert fakes.classify_calls == []
    assert fakes.ended == [DEAD_END_REASON]
    assert runtime.ended is True
    assert any("b1" in record.getMessage() for record in caplog.records if record.levelno >= 40)


def test_a_conversation_node_with_nowhere_to_go_still_stays_put() -> None:
    """The other half of the rule: a node that CAN hold a conversation is not
    a dead end. It has instructions and tools installed and the model keeps
    talking, so there is nothing to end.
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "How can I help?"},
            }
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    _run(runtime.on_user_turn())

    assert runtime.current_node_id == "n1"
    assert runtime.ended is False
    assert fakes.ended == []


# ---------------------------------------------------------------------------
# skip_response_edge / always_edge
# ---------------------------------------------------------------------------


def test_a_skip_response_edge_speaks_then_transitions_without_waiting() -> None:
    """Corrected reading: ``skip_response_edge`` means skip WAITING FOR the
    caller's response, not skip speaking. The node says its own line (a
    ``static_text`` verbatim, a ``prompt`` as a model-turn request) exactly
    like any other node, and only then — with no user turn in between —
    follows the skip_response_edge. Do not revert this to "records no `say`
    call": that was the wrong reading, disproved by the real
    ``prior_auth_hotline.json`` fixture (see
    ``test_the_real_fixtures_skip_response_nodes_now_speak_before_advancing``).
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "There is no case on file."},
                "edges": [],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Ask what else they need."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "n2"
    # The static line WAS spoken -- this is the whole point of the fix.
    assert fakes.said == ["There is no case on file."]
    # n1 was installed too (like any other node); n2 follows automatically,
    # with no user turn (no on_user_turn()/advance() call) in between.
    assert [node_id for node_id, _edges in fakes.build_calls] == ["n1", "n2"]
    assert len(fakes.instructions) == 2
    # n1's static_text line was said verbatim and asked for no model turn of
    # its own. The single recorded turn is n2's: the cascade lands there
    # while `start()` is still opening the call (`_opening`), and n2's line is
    # a ``prompt``, so without it the agent would state "There is no case on
    # file." and then fall silent instead of asking what else they need. See
    # ``test_the_opening_turn_survives_a_skip_chain_to_a_terminal_prompt_node``.
    assert fakes.said == ["There is no case on file."]
    assert fakes.generate_reply_calls == [None]


def test_a_skip_response_edge_with_a_prompt_instruction_requests_a_model_turn_before_advancing() -> (
    None
):
    """The production-bug case: a ``prompt`` line with no other exit but
    ``skip_response_edge`` must not be silently dropped. Since no user turn
    follows before the auto-advance, the runtime must itself ask for a model
    turn to voice the line.
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Say there is no case on file."},
                "edges": [],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Anything else?"},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "n2"
    # No say() for the prompt line -- it was requested as a model turn instead,
    # carrying n1's OWN instructions rather than "reply from whatever is
    # installed" (by the time the turn runs, n2's are).
    assert fakes.said == ["Anything else?"]
    assert len(fakes.generate_reply_calls) == 1
    assert "Say there is no case on file." in (fakes.generate_reply_calls[0] or "")


def test_a_chain_of_skip_response_edges_stops_at_the_automatic_transition_budget(
    caplog,
) -> None:
    """A run of skip_response-only nodes must not spin forever inside one turn."""
    chain_length = MAX_AUTOMATIC_TRANSITIONS + 5
    nodes = []
    for i in range(chain_length):
        nodes.append(
            {
                "id": f"n{i}",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": f"Line {i}."},
                "edges": [],
                "skip_response_edge": {
                    "id": f"skip-{i}",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": f"n{i + 1}",
                },
            }
        )
    # Final node has no skip_response_edge, so the chain would stop there on
    # its own if the budget didn't cut it off first -- it shouldn't get that
    # far.
    nodes.append({"id": f"n{chain_length}", "type": "conversation", "instruction": None})

    flow = {"start_node_id": "n0", "nodes": nodes}
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    with caplog.at_level(logging.ERROR, logger=FLOW_LOGGER):
        _run(runtime.start())

    # Exactly MAX_AUTOMATIC_TRANSITIONS auto-follows happened: the entry into
    # n0 is not itself a transition, so the walk stops at node
    # `MAX_AUTOMATIC_TRANSITIONS`, never reaching the end of the chain.
    assert runtime.current_node_id == f"n{MAX_AUTOMATIC_TRANSITIONS}"
    assert len(fakes.said) == MAX_AUTOMATIC_TRANSITIONS + 1
    assert any(
        record.levelno == logging.ERROR and "automatic transitions" in record.getMessage()
        for record in caplog.records
    )


def test_a_node_with_its_own_prompt_edges_keeps_its_turn_despite_a_skip_response_edge() -> None:
    """Skipping a node that gives the model a real choice would strand that choice."""
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Anything else?"},
                "edges": [
                    {
                        "id": "p1",
                        "transition_condition": _prompt_condition("Caller needs more help"),
                        "destination_node_id": "n3",
                    }
                ],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {"id": "n2", "type": "conversation", "instruction": {"type": "prompt", "text": "Two."}},
            {
                "id": "n3",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Three."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "n1"
    assert fakes.said == ["Anything else?"]
    assert _ids(fakes.offered_edges) == ["p1"]


def test_a_node_offered_only_a_synthetic_global_edge_still_auto_advances() -> None:
    """A synthetic ``global::`` edge must not strand a node -- this is the regression.

    n1 has no *authored* prompt edges of its own, only the synthetic
    ``global::g1`` edge that `prompt_edges` offers at every node for the
    flow's one global node. A global node is reachable from anywhere with no
    authored edge -- it is an escape hatch ("caller wants a human"), not a
    choice n1's own author put in front of the model -- and g1 stays just as
    reachable from n2 as it was from n1, so nothing is lost by advancing.
    Counting that synthetic edge in the stranding guard (the bug this test
    guards against) would wrongly keep n1's turn forever, exactly as it did
    for every skip-only node in the real ``prior_auth_hotline.json`` fixture
    before this fix.
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Anything else?"},
                "edges": [],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {"id": "n2", "type": "conversation", "instruction": {"type": "prompt", "text": "Two."}},
            {
                "id": "g1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Transferring you to a human."},
                "global_node_setting": {"condition": "Caller wants a human"},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "n2"
    assert fakes.said == ["Anything else?"]
    # g1 is still offered at the destination node -- nothing about the
    # escape hatch was lost by advancing past n1.
    assert _ids(fakes.offered_edges) == ["global::g1"]


def test_an_equation_edge_beats_the_skip_response_edge() -> None:
    """Equation edges take precedence at every transition point, including here."""
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Verifying member."},
                "edges": [
                    {
                        "id": "eq-verified",
                        "transition_condition": _equation_condition("{{verified}}", "==", "yes"),
                        "destination_node_id": "n3",
                    }
                ],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {"id": "n2", "type": "conversation", "instruction": {"type": "prompt", "text": "Two."}},
            {
                "id": "n3",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Three."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes, variables={"verified": "yes"})
    _run(runtime.start())

    assert runtime.current_node_id == "n3"
    assert fakes.said == ["Verifying member."]


# The four `prior_auth_hotline.json` nodes whose only exit is a
# skip_response_edge, and the static line each one is supposed to say. Under
# the old (wrong) "never speaks" reading these were silently skipped; the
# corrected reading (skip response == skip WAITING, not skip speaking) means
# every one of these lines must now actually reach the caller.
#
# Three of the four (``node-1773865396835`` "No Case", ``node-1773865855589``
# "Conversation", ``node-1774302031808`` "Transfer") route their
# skip_response_edge at the "Working Hour Split Node"
# (``node-1773864774353``, a ``branch``); the fourth (``node-1773866072757``
# "Please stay on the line") routes straight to the transfer_call node
# instead and never touches the branch.
_SKIP_RESPONSE_STATIC_LINES: dict[str, str] = {
    "node-1773865396835": (
        "I'm seeing that member, but I'm not seeing any case information for "
        "them. Let me transfer you to a specialist to help you further"
    ),
    "node-1773865855589": (
        "I don't have additional information beyond what is in the system. "
        "Let me transfer you to a specialist to help you further."
    ),
    "node-1773866072757": "Please stay on the line while I transfer you",
    "node-1774302031808": (
        "It looks like we couldn't get your case information right now. Let me "
        "transfer you to a specialist to help you further."
    ),
}

# "Please stay on the line while I transfer you" -- the second line spoken by
# the three nodes that route through the working-hour branch on their way to
# the transfer.
_STAY_ON_THE_LINE = "Please stay on the line while I transfer you"

# The transfer_call node ("Transfer Call") every one of the four skip-only
# nodes ultimately reaches: directly for node-1773866072757, or via the
# working-hour branch's ``else_edge`` for the other three.
_TRANSFER_CALL_NODE_ID = "node-1773866123876"


def test_the_real_fixtures_skip_response_nodes_now_speak_before_advancing(
    prior_auth_flow,
) -> None:
    """The four previously-silent static lines are now actually spoken, and
    the cascade they used to (and, with this fix, once again) trigger runs
    all the way to the transfer.

    Each of these nodes carries a static line plus a skip_response_edge and
    no other *authored* exit of its own (``edges: []``): with the stranding
    guard fixed to count only authored prompt edges, none of the four has
    anything of its own to strand on, so each speaks its line and
    auto-follows its skip_response_edge without waiting for a user turn.

    TRACE NOTE, since prior reports have disagreed here (both "two lines
    each" and "one line each" have been claimed at different points): a fresh
    trace with this fix applied settles it. "Working Hour Split Node"
    (``node-1773864774353``) is itself a global node, so it is also offered
    a synthetic ``global::node-1773864774353`` edge at every *other* node in
    the flow -- but a synthetic global edge is exactly what this fix says
    must NOT strand a node, so it does not stop any of the four here either.
    Three of the four (all but "Please stay on the line") land on the branch
    node next; its own office-hours edge is a ``prompt`` condition, so
    `classify` is asked once, and the fixture's default "no match" answer
    (`Fakes` with no configured results returns ``None``) falls through the
    branch's ``else_edge`` onto "Please stay on the line" -- a *second*
    spoken line -- which itself then auto-follows its own skip_response_edge
    onto the transfer_call node. That node has ``speak_during_execution``
    false, so it adds no further line, and the transfer succeeds (the
    default `Fakes` transfer result), landing all four traces on the same
    transfer_call node. So: three nodes speak two lines and call `classify`
    once; the fourth speaks one line and calls `classify` zero times; all
    four end up transferred to the same node.
    """
    for node_id, expected_line in _SKIP_RESPONSE_STATIC_LINES.items():
        fakes = Fakes()
        runtime = _runtime(prior_auth_flow, fakes)
        _run(runtime.advance({"id": "x", "destination_node_id": node_id}))

        if node_id == "node-1773866072757":
            assert fakes.said == [expected_line], node_id
            assert fakes.classify_calls == [], node_id
        else:
            assert fakes.said == [expected_line, _STAY_ON_THE_LINE], node_id
            assert len(fakes.classify_calls) == 1, node_id

        assert fakes.transfers == ["+15555550101"], node_id
        assert runtime.current_node_id == _TRANSFER_CALL_NODE_ID, node_id


def test_an_always_edge_fires_on_the_next_user_turn_and_is_never_offered_to_the_model() -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Confirm the details."},
                "edges": [
                    {
                        "id": "p1",
                        "transition_condition": _prompt_condition("Details wrong"),
                        "destination_node_id": "n3",
                    }
                ],
                "always_edge": {
                    "id": "always-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Moving on."},
            },
            {"id": "n3", "type": "end"},
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    # Entering does not fire the always edge, and the model is never offered it.
    assert runtime.current_node_id == "n1"
    assert _ids(fakes.offered_edges) == ["p1"]
    assert "always-1" not in fakes.instructions[0]

    _run(runtime.on_user_turn())
    assert runtime.current_node_id == "n2"
    assert fakes.said == ["Moving on."]
    assert fakes.classify_calls == []


# ---------------------------------------------------------------------------
# equation edges on a user turn
# ---------------------------------------------------------------------------


def test_an_equation_edge_that_becomes_true_transitions_on_the_next_user_turn() -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Verify the member."},
                "edges": [
                    {
                        "id": "eq-verified",
                        "transition_condition": _equation_condition("{{verified}}", "==", "yes"),
                        "destination_node_id": "n2",
                    }
                ],
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Verified, thank you."},
            },
        ],
    }
    fakes = Fakes()
    variables: dict[str, Any] = {}
    runtime = _runtime(flow, fakes, variables=variables)
    _run(runtime.start())
    assert runtime.current_node_id == "n1"

    _run(runtime.on_user_turn())
    assert runtime.current_node_id == "n1"

    variables["verified"] = "yes"
    _run(runtime.on_user_turn())
    assert runtime.current_node_id == "n2"
    assert fakes.said == ["Verified, thank you."]
    # An equation edge never reaches the model.
    assert fakes.classify_calls == []
    assert _ids(fakes.build_calls[0][1]) == []


def test_a_speaking_node_delivers_its_line_before_any_equation_edge_is_considered() -> None:
    """Entering a speaking node is not a transition point.

    The equation below is already true on entry. Evaluating it there would
    swallow the greeting and leave the caller with dead air, so the line is
    delivered first and the transition happens on the following user turn.
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Welcome to the hotline."},
                "edges": [
                    {
                        "id": "eq-known",
                        "transition_condition": _equation_condition("{{member_id}}", "exists", ""),
                        "destination_node_id": "n2",
                    }
                ],
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "I already have your details."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes, variables={"member_id": "M-1"})
    _run(runtime.start())

    assert runtime.current_node_id == "n1"
    assert fakes.said == ["Welcome to the hotline."]

    _run(runtime.on_user_turn())
    assert runtime.current_node_id == "n2"
    assert fakes.said == ["Welcome to the hotline.", "I already have your details."]


# ---------------------------------------------------------------------------
# logging, loop bound, robustness
# ---------------------------------------------------------------------------


def test_every_transition_is_logged_with_the_call_id_and_both_node_ids(caplog) -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Ask."},
                "edges": [
                    {
                        "id": "p1",
                        "transition_condition": _prompt_condition("Answered"),
                        "destination_node_id": "n2",
                    }
                ],
            },
            {"id": "n2", "type": "conversation", "instruction": {"type": "prompt", "text": "Ok."}},
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes, call_id="call_xyz")
    _run(runtime.start())
    with caplog.at_level(logging.INFO, logger=FLOW_LOGGER):
        _run(runtime.advance(flow["nodes"][0]["edges"][0]))

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "call_xyz" in message and "n1" in message and "n2" in message for message in messages
    ), messages


def test_a_cycle_of_silent_nodes_stops_at_the_automatic_transition_bound(caplog) -> None:
    always_true = _equation_condition("1", "==", "1")
    flow = {
        "start_node_id": "b1",
        "nodes": [
            {
                "id": "b1",
                "type": "branch",
                "edges": [
                    {
                        "id": "to-b2",
                        "transition_condition": always_true,
                        "destination_node_id": "b2",
                    }
                ],
            },
            {
                "id": "b2",
                "type": "branch",
                "edges": [
                    {
                        "id": "to-b1",
                        "transition_condition": always_true,
                        "destination_node_id": "b1",
                    }
                ],
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    with caplog.at_level(logging.INFO, logger=FLOW_LOGGER):
        _run(runtime.start())

    transitions = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and "->" in record.getMessage()
    ]
    assert len(transitions) == MAX_AUTOMATIC_TRANSITIONS
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert fakes.said == []
    assert fakes.classify_calls == []
    # A user turn refreshes the budget rather than wedging the call forever.
    with caplog.at_level(logging.INFO, logger=FLOW_LOGGER):
        _run(runtime.on_user_turn())
    assert runtime.current_node_id in ("b1", "b2")


def test_advancing_along_a_dangling_edge_stays_put(caplog) -> None:
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "conversation", "instruction": {"type": "prompt", "text": "Hi"}}
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    with caplog.at_level(logging.WARNING, logger=FLOW_LOGGER):
        _run(runtime.advance({"id": "dangling"}))

    assert runtime.current_node_id == "n1"
    assert caplog.records


def test_advance_drops_a_stale_edge_requested_from_a_node_already_left() -> None:
    """FIX 2 regression: a spawned advance can act on a stale edge.

    Two parallel `transition_to` calls from the same node each capture their
    `from_node_id` at request time. The first walks A -> B and enters B; the
    second must be dropped -- not walked from B -- because the cursor has
    moved since it was requested. A subsequent, legitimate sequential advance
    (request -> complete -> request again, `from_node_id` matching the node
    the runtime is actually on) must still work.
    """
    flow = {
        "start_node_id": "a",
        "nodes": [
            {"id": "a", "type": "conversation", "instruction": None},
            {
                "id": "b",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "In B."},
            },
            {
                "id": "c",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "In C."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    assert runtime.current_node_id == "a"

    edge_to_b = {"id": "e-ab", "destination_node_id": "b"}
    edge_to_c = {"id": "e-ac", "destination_node_id": "c"}

    # Two transitions requested "at the same time" from node "a".
    _run(runtime.advance(edge_to_b, from_node_id="a"))
    assert runtime.current_node_id == "b"

    # The second is stale: the cursor moved to "b" before it ran.
    _run(runtime.advance(edge_to_c, from_node_id="a"))
    assert runtime.current_node_id == "b"
    assert fakes.said == ["In B."]  # "In C." never spoken -- the second was dropped

    # A legitimate sequential advance (request -> complete -> request again)
    # from wherever the runtime now actually is must still work.
    _run(runtime.advance(edge_to_c, from_node_id="b"))
    assert runtime.current_node_id == "c"
    assert fakes.said == ["In B.", "In C."]


def test_advance_with_no_from_node_id_is_unguarded() -> None:
    """Back-compat: omitting `from_node_id` (every pre-existing call site)
    must behave exactly as before -- no stale-edge check at all.
    """
    flow = {
        "start_node_id": "a",
        "nodes": [
            {"id": "a", "type": "conversation", "instruction": None},
            {
                "id": "b",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "In B."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    _run(runtime.advance({"id": "e-ab", "destination_node_id": "b"}))
    assert runtime.current_node_id == "b"
    assert fakes.said == ["In B."]


def test_a_synthetic_global_edge_routes_by_destination_not_by_edge_id() -> None:
    """`prompt_edges` synthesizes `global::<id>` edge ids that are not node ids."""
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Ask."},
            },
            {
                "id": "g1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Transferring you to a human."},
                "global_node_setting": {"condition": "Caller wants a human"},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    offered = fakes.offered_edges
    assert _ids(offered) == ["global::g1"]

    _run(runtime.advance(offered[0]))
    assert runtime.current_node_id == "g1"
    assert fakes.said == ["Transferring you to a human."]


def test_start_speaker_user_defers_the_greeting_to_the_first_user_turn() -> None:
    flow = {
        "start_node_id": "n1",
        "start_speaker": "user",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Hello, how can I help?"},
            }
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    assert fakes.said == []
    assert fakes.tools  # instructions/tools are installed regardless

    _run(runtime.on_user_turn())
    assert fakes.said == ["Hello, how can I help?"]


def test_start_speaker_user_accumulates_pending_speech_across_a_skip_chain() -> None:
    """A skip_response_edge cascade during `start()` must not drop earlier lines.

    `_defer_speech` stays true for the whole `start()` call (it is only reset
    in its ``finally``), so a chain of two nodes each parking a line while
    deferred must not have the second overwrite the first: both are still
    owed to the caller once the first user turn arrives.
    """
    flow = {
        "start_node_id": "n1",
        "start_speaker": "user",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "First line."},
                "edges": [],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Second line."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    # Nothing spoken yet -- both lines are still pending the first user turn.
    assert fakes.said == []

    _run(runtime.on_user_turn())
    assert fakes.said == ["First line.", "Second line."]


def test_start_speaker_user_defers_a_prompt_instructions_model_turn_too() -> None:
    """`_generate_reply` must be gated by `_defer_speech` exactly like `_speak_static` is.

    A start node with ``start_speaker: "user"``, a ``prompt`` instruction,
    and a ``skip_response_edge`` (so nothing but the runtime itself would
    ever trigger the model turn) must not have the agent speak first: the
    turn is parked until the caller's first turn, mirroring
    ``test_a_skip_response_edge_with_a_prompt_instruction_requests_a_model_turn_before_advancing``
    but with the greeting deferred.
    """
    flow = {
        "start_node_id": "n1",
        "start_speaker": "user",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Say there is no case on file."},
                "edges": [],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Anything else?"},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    # Deferred: no model turn requested yet, and nothing said -- the agent
    # must not open the conversation.
    assert fakes.generate_reply_calls == []
    assert fakes.said == []

    _run(runtime.on_user_turn())
    # Released in order: the deferred model turn (n1), then the deferred
    # static line (n2) parked right behind it during the same skip cascade.
    #
    # And the parked turn carries n1's instructions EXPLICITLY. This is the
    # whole reason the deferred case needs its own test: `None` means "reply
    # from whatever is installed", and by the time the deferral is released
    # the cascade has installed n2's instructions -- so n1's line ("Say there
    # is no case on file.") would never be phrased at all.
    assert len(fakes.generate_reply_calls) == 1
    assert "Say there is no case on file." in (fakes.generate_reply_calls[0] or "")
    assert fakes.said == ["Anything else?"]


def test_an_agent_opened_start_node_with_a_prompt_instruction_requests_a_model_turn() -> None:
    """Regression: a `prompt` start node used to open the call in silence.

    `_speak_static` only voices a ``static_text`` instruction; a ``prompt``
    one needs a model turn. That turn used to be requested ONLY on the
    ``skip_response_edge`` path, so a plain start node carrying a ``prompt``
    instruction said nothing at all — and `main.py` builds a flow-backed
    agent with ``start_speaker="user"`` precisely so `ArhiteqAgent.on_enter`
    leaves the opening line to the flow, meaning nothing else covered it
    either. On an outbound call that is dead air until the callee hangs up.
    """
    flow = {
        "start_node_id": "n1",
        "start_speaker": "agent",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Greet the caller warmly."},
                "edges": [],
            }
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    # Instructions are installed, so the turn is asked for from them (None).
    assert fakes.instructions and "Greet the caller warmly." in fakes.instructions[0]
    assert fakes.generate_reply_calls == [None]
    assert fakes.said == []  # nothing verbatim to say: the model phrases it
    assert runtime.current_node_id == "n1"


def test_a_static_text_start_node_still_speaks_without_a_model_turn() -> None:
    """The other half of the pair: a verbatim line must not ALSO ask for a turn."""
    flow = {
        "start_node_id": "n1",
        "start_speaker": "agent",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Thanks for calling."},
                "edges": [],
            }
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    assert fakes.said == ["Thanks for calling."]
    assert fakes.generate_reply_calls == []


def test_start_speaker_user_does_not_request_an_opening_model_turn() -> None:
    """The opening turn is for the agent opening — the caller opening is not it.

    With ``start_speaker: "user"`` the caller opens by definition, and their
    first turn drives livekit's ordinary reply from the instructions
    `_install` set. Requesting (or parking) an opening turn here as well
    would have the agent answer twice.
    """
    flow = {
        "start_node_id": "n1",
        "start_speaker": "user",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Greet the caller warmly."},
                "edges": [],
            }
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    assert fakes.generate_reply_calls == []
    assert fakes.said == []

    # ...and the first user turn must not release a parked one either: there
    # is nothing parked, because nothing was ever deferred.
    _run(runtime.on_user_turn())
    assert fakes.generate_reply_calls == []
    assert fakes.said == []


def test_a_prompt_instruction_node_entered_mid_call_does_not_request_a_model_turn() -> None:
    """Only the OPENING gets an explicit turn; a transition already has one coming.

    A node entered by `advance` was reached from a tool call or a user turn,
    and livekit produces the reply for that turn itself. Asking for a second
    one here would talk over it — which is why the opening turn is gated on
    `_opening` rather than "any prompt instruction with nothing said".
    """
    flow = {
        "start_node_id": "n1",
        "start_speaker": "agent",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Thanks for calling."},
                "edges": [
                    {
                        "id": "e1",
                        "transition_condition": _prompt_condition("Caller is ready"),
                        "destination_node_id": "n2",
                    }
                ],
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Ask for their date of birth."},
                "edges": [],
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    assert fakes.generate_reply_calls == []

    _run(runtime.advance(fakes.offered_edges[0]))
    assert runtime.current_node_id == "n2"
    assert fakes.generate_reply_calls == []  # livekit's own post-tool turn covers it


def test_the_opening_turn_survives_a_skip_chain_to_a_terminal_prompt_node() -> None:
    """`_opening` spans the whole of `start()`, cascade included.

    A start node that skips straight into a ``prompt`` node is still opening
    the call when it lands there: that node is the one that actually greets
    the caller, and nothing downstream of `start()` will trigger its turn.
    """
    flow = {
        "start_node_id": "n1",
        "start_speaker": "agent",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "One moment."},
                "edges": [],
                "skip_response_edge": {
                    "id": "skip-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "Greet the caller warmly."},
                "edges": [],
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "n2"
    assert fakes.said == ["One moment."]
    assert fakes.generate_reply_calls == [None]


# ---------------------------------------------------------------------------
# The real Retell fixtures actually open the call
# ---------------------------------------------------------------------------


def _production_runtime(flow_dict: dict[str, Any], fakes: Fakes) -> FlowRuntime:
    """`_runtime`, but with `main.py`'s start-node start_speaker override applied.

    `_FlowWiring.attach` hands `FlowRuntime` a config whose ``start_speaker``
    has already been resolved through `start_speaker_for` (the start node may
    override the flow's own). The fixture tests below assert on who opens the
    call, so they have to see the same value production does — not the raw
    flow field `_runtime` passes through.
    """
    from dataclasses import replace

    from arhiteq_worker.flow import start_speaker_for

    config = ConversationFlowConfig.from_dict(flow_dict)
    graph = FlowGraph.from_config(config)
    config = replace(config, start_speaker=start_speaker_for(graph.start, config.start_speaker))
    return FlowRuntime(
        graph,
        config,
        {},
        set_instructions=fakes.set_instructions,
        set_tools=fakes.set_tools,
        say=fakes.say,
        classify=fakes.classify,
        build_node_tools=fakes.build_node_tools,
        end_call=fakes.end_call,
        transfer_call=fakes.transfer_call,
        generate_reply=fakes.generate_reply,
        call_id="call_abc",
    )


@pytest.mark.parametrize(
    "fixture_name",
    ["prior_auth_hotline.json", "clara_outbound.json", "identity_verify_transfer.json"],
)
def test_every_real_fixture_opens_the_call_with_something(fixture_name: str) -> None:
    """No real flow may enter its start node and then just sit there silently.

    This is the check that would have caught the ``prompt``-instruction
    silence: `prior_auth_hotline.json` opens on a ``static_text`` node (so it
    always worked and was the one the manual live call exercised), while
    `clara_outbound.json` and `identity_verify_transfer.json` open on
    ``prompt`` nodes and said nothing at all. All three carry
    ``start_speaker: "agent"``, so in every case the agent is the one meant
    to open.
    """
    from conftest import load_retell_flow_fixture

    fakes = Fakes()
    runtime = _production_runtime(load_retell_flow_fixture(fixture_name), fakes)
    _run(runtime.start())

    assert fakes.instructions, "the start node installed no instructions"
    assert fakes.said or fakes.generate_reply_calls, (
        f"{fixture_name} enters its start node without speaking or requesting "
        "a model turn: the call opens in silence"
    )
    assert not runtime.ended


def test_the_outbound_fixture_opens_with_a_model_turn_not_a_verbatim_line() -> None:
    """`clara_outbound.json` — the regression's worst case, pinned specifically.

    Its start node is a ``prompt`` instruction, so the opening line is
    phrased by the model rather than spoken verbatim. An outbound call that
    opens in silence is one the callee hangs up on.
    """
    from conftest import load_retell_flow_fixture

    fakes = Fakes()
    runtime = _production_runtime(load_retell_flow_fixture("clara_outbound.json"), fakes)
    _run(runtime.start())

    assert fakes.said == []
    assert fakes.generate_reply_calls == [None]
    assert "Clara" in fakes.instructions[0]


def test_on_user_turn_before_start_is_a_no_op() -> None:
    """FIX 1 regression: the first user turn can beat `start()`.

    `on_user_turn` gets wired into the live session, and `start()` is
    spawned separately (see `_FlowWiring` in `main.py`) -- if the caller
    speaks before `start()` has run, `on_user_turn` must not evaluate the
    start node's edges (it hasn't been entered yet: no instructions, no
    tools, nothing to react to). It must be a total no-op, and the
    subsequent `start()` must still enter the start node normally
    afterwards.
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Hello, how can I help?"},
                "always_edge": {
                    "id": "always-1",
                    "transition_condition": _prompt_condition("Always"),
                    "destination_node_id": "n2",
                },
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Moving on."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)

    # The caller's first turn lands before `start()` has run.
    _run(runtime.on_user_turn())

    assert runtime.current_node_id == "n1"
    assert fakes.instructions == []
    assert fakes.tools == []
    assert fakes.said == []

    # `start()` must still enter the start node normally afterwards.
    _run(runtime.start())

    assert runtime.current_node_id == "n1"
    assert fakes.said == ["Hello, how can I help?"]
    assert len(fakes.instructions) == 1


def test_nothing_is_spoken_after_the_call_has_ended() -> None:
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
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    _run(runtime.on_user_turn())
    _run(runtime.advance({"id": "x", "destination_node_id": "n1"}))

    assert fakes.said == ["Goodbye."]
    assert fakes.ended == ["agent_hangup"]


# ---------------------------------------------------------------------------
# The real 18-node prior-auth fixture
# ---------------------------------------------------------------------------

# One realistic provider-office path: welcome -> subagent -> get_member ->
# get_pa_cases -> medication name -> confirm -> read status -> wrap up -> end.
_PRIOR_AUTH_SCRIPT: list[tuple[str, str]] = [
    ("edge-1", "node-1775762962094"),
    ("edge-1775762962094-xixwi43d4", "node-1773865257897"),
    ("edge-1773865278073-xlfba5viz", "node-1773865358553"),
    ("edge-1773865366189-tq0bpqx96", "node-1773865454857"),
    ("edge-1773865454857-qqze7bzc8", "node-1773865644082"),
    ("edge-1773865644082-qpnxaup0f", "node-1773865693216"),
    ("edge-1773865693216-4yignd2r2", "node-1773865712958"),
    ("edge-1773865712958-flfluqrwg", "node-1773865762237"),
]


def test_walking_the_real_prior_auth_fixture_ends_on_an_end_node(prior_auth_flow) -> None:
    fakes = Fakes()
    config = ConversationFlowConfig.from_dict(prior_auth_flow)
    graph = FlowGraph.from_config(config)
    runtime = _runtime(
        prior_auth_flow,
        fakes,
        variables={"member_id": "M-1", "date_of_birth": "2000-01-01"},
    )

    _run(runtime.start())
    assert runtime.current_node_id == prior_auth_flow["start_node_id"]

    for edge_id, expected_destination in _PRIOR_AUTH_SCRIPT:
        node_id, offered = fakes.build_calls[-1]
        assert node_id == runtime.current_node_id
        chosen = next((edge for edge in offered if edge.get("id") == edge_id), None)
        assert chosen is not None, f"{edge_id} was not offered at {node_id}: {_ids(offered)}"
        # A user turn happens before the model picks a transition.
        _run(runtime.on_user_turn())
        _run(runtime.advance(chosen))
        assert runtime.current_node_id == expected_destination

    assert graph.node(runtime.current_node_id)["type"] == "end"
    assert fakes.ended == ["agent_hangup"]
    assert fakes.said[0].startswith("Thank you for calling the Retell prior authorization hotline")
    assert fakes.said[-1] == 'Thank you for calling Retell and have a wonderful day!"'


def test_the_real_fixtures_stay_on_the_line_node_auto_transfers(prior_auth_flow) -> None:
    """ "Please stay on the line" speaks, then auto-follows straight to the
    transfer -- it has no *authored* prompt edges to strand, only the
    synthetic ``global::node-1773864774353`` edge every node in this flow is
    offered for the (itself-global) working-hour-split branch, and a
    synthetic global edge must not keep a node's turn (see the docstring on
    `_enter_conversation` and the trace note on
    ``test_the_real_fixtures_skip_response_nodes_now_speak_before_advancing``).
    """
    fakes = Fakes()
    runtime = _runtime(prior_auth_flow, fakes)
    _run(runtime.advance({"id": "x", "destination_node_id": "node-1773866072757"}))

    assert fakes.said == ["Please stay on the line while I transfer you"]
    assert fakes.classify_calls == []
    assert fakes.transfers == ["+15555550101"]
    assert runtime.current_node_id == _TRANSFER_CALL_NODE_ID


def test_the_real_fixtures_failed_transfer_ends_the_call_rather_than_stranding_the_caller(
    prior_auth_flow, caplog
) -> None:
    """The live-caller case this whole rule exists for.

    ``node-1773866123876`` ("Transfer Call") carries a single failure ``edge``
    with NO ``destination_node_id`` — a dangling edge in the real sanitized
    Retell capture, which `fallback_edge` rightly reports as ``None`` (there is
    nowhere to send the call) and which `FlowGraph.from_config` cannot reject at
    load (there is no destination to validate). `CallRuntime.transfer_call`
    fails on every non-SIP call — i.e. every web call — so this path is not
    exotic. Before the fix the runtime logged "staying put" and returned: the
    agent still held the previous node's instructions and tools, ``_ended`` was
    false, and the caller heard nothing at all until the inactivity watchdog
    fired. Ending the call is the honest outcome.
    """
    fakes = Fakes(transfer_result=json.dumps({"error": "transfer not supported on this call"}))
    runtime = _runtime(prior_auth_flow, fakes)
    with caplog.at_level(logging.ERROR, logger=FLOW_LOGGER):
        _run(runtime.advance({"id": "x", "destination_node_id": _TRANSFER_CALL_NODE_ID}))

    assert fakes.transfers == ["+15555550101"]
    assert fakes.ended == [DEAD_END_REASON]
    assert runtime.ended is True
    assert any(
        _TRANSFER_CALL_NODE_ID in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
    )


# ---------------------------------------------------------------------------
# A flow's default_dynamic_variables
# ---------------------------------------------------------------------------


def _defaults_flow() -> dict[str, Any]:
    return {
        "start_node_id": "n1",
        "default_dynamic_variables": {"caller_name": "Dana", "plan_tier": "gold"},
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Hi {{caller_name}}."},
                "edges": [
                    {
                        "id": "e-gold",
                        "transition_condition": _equation_condition("{{plan_tier}}", "==", "gold"),
                        "destination_node_id": "n2",
                    }
                ],
            },
            {
                "id": "n2",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Connecting you to the gold desk."},
            },
        ],
    }


def _flow_call_variables(flow: dict[str, Any], **dynamic_variables: Any) -> Any:
    """The live variables mapping a flow-backed call actually runs on."""
    cfg = CallConfig.from_dict(
        {
            "call_id": "call_abc",
            "call_type": "web_call",
            "conversation_flow": flow,
            "dynamic_variables": dynamic_variables,
        }
    )
    return cfg.resolution_variables()


def test_a_flows_default_dynamic_variables_reach_the_live_call() -> None:
    """Nothing read ``default_dynamic_variables`` end to end before this.

    A flow-backed agent has ``llm: null``, so the control plane's own
    defaults-merge (which only runs when an LLM exists) never fires for it:
    the flow's defaults reached the worker in the call config and were then
    dropped on the floor. Two visible consequences, both asserted here — the
    greeting speaks the raw ``{{caller_name}}`` placeholder, and every
    ``equation`` edge testing a defaulted variable reads *missing* (hence
    False), silently degrading equation routing to the else/fallback path.
    """
    flow = _defaults_flow()
    fakes = Fakes()
    runtime = _runtime(flow, fakes, variables=_flow_call_variables(flow))

    _run(runtime.start())
    assert fakes.said == ["Hi Dana."]

    # The equation edge tests a variable that only the flow's defaults supply.
    _run(runtime.on_user_turn())
    assert runtime.current_node_id == "n2"
    assert fakes.said == ["Hi Dana.", "Connecting you to the gold desk."]


def test_a_call_level_variable_still_beats_the_flows_default() -> None:
    """Precedence matches the single-prompt path: defaults < call-level."""
    flow = _defaults_flow()
    fakes = Fakes()
    runtime = _runtime(
        flow, fakes, variables=_flow_call_variables(flow, caller_name="Renata", plan_tier="silver")
    )

    _run(runtime.start())
    _run(runtime.on_user_turn())

    assert fakes.said == ["Hi Renata."]
    assert runtime.current_node_id == "n1"  # plan_tier is silver: no gold edge


# ---------------------------------------------------------------------------
# Deferred speech must not be lost on the way out
# ---------------------------------------------------------------------------


def test_an_end_node_flushes_speech_deferred_by_start_speaker_user() -> None:
    """``start_speaker: "user"`` parks the start node's line in
    `_pending_speech`; `_speak_static` reports it as spoken, so an ``end``
    node downstream of a ``skip_response_edge`` chain hung up on a queue that
    nothing ever drained and the caller heard neither line.
    """
    flow = {
        "start_node_id": "n1",
        "start_speaker": "user",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "One moment."},
                "skip_response_edge": {
                    "id": "skip",
                    "transition_condition": _prompt_condition("Skip response"),
                    "destination_node_id": "n_end",
                },
            },
            {
                "id": "n_end",
                "type": "end",
                "speak_during_execution": True,
                "instruction": {"type": "static_text", "text": "Goodbye."},
            },
        ],
    }
    fakes = Fakes()
    runtime = _runtime(flow, fakes)
    _run(runtime.start())

    assert fakes.said == ["One moment.", "Goodbye."]
    assert fakes.ended == ["agent_hangup"]


# ---------------------------------------------------------------------------
# The closing line must not be pre-empted by the previous node's tools
# ---------------------------------------------------------------------------


def _closing_line_events(fakes: Fakes) -> list[str]:
    """Record the order of `set_tools` / `generate_reply` calls on *fakes*."""
    events: list[str] = []
    original_set_tools = fakes.set_tools
    original_generate_reply = fakes.generate_reply

    async def set_tools(tools: list[Any]) -> None:
        events.append(f"set_tools:{len(tools)}")
        await original_set_tools(tools)

    async def generate_reply(instructions: str | None) -> None:
        events.append("generate_reply")
        await original_generate_reply(instructions)

    fakes.set_tools = set_tools  # type: ignore[method-assign]
    fakes.generate_reply = generate_reply  # type: ignore[method-assign]
    return events


def test_an_end_node_drops_the_previous_nodes_tools_before_its_closing_turn() -> None:
    """Otherwise the model can call the *previous* node's still-installed
    ``transition_to`` instead of voicing the closing line — and that spawned
    advance then races the hang-up right below it.
    """
    flow = {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Anything else?"},
                "edges": [
                    {
                        "id": "e-done",
                        "transition_condition": _prompt_condition("Caller is done"),
                        "destination_node_id": "n_end",
                    }
                ],
            },
            {
                "id": "n_end",
                "type": "end",
                "speak_during_execution": True,
                "instruction": {"type": "prompt", "text": "Thank the caller and say goodbye."},
            },
        ],
    }
    fakes = Fakes()
    events = _closing_line_events(fakes)
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    _run(runtime.advance({"id": "e-done", "destination_node_id": "n_end"}))

    assert events[-2:] == ["set_tools:0", "generate_reply"]
    assert fakes.ended == ["agent_hangup"]


def test_a_transfer_node_drops_the_previous_nodes_tools_before_its_closing_turn() -> None:
    fakes = Fakes()
    events = _closing_line_events(fakes)
    runtime = _runtime(
        _transfer_flow(
            {"type": "predefined", "number": "+15555550101"},
            speak_during_execution=True,
            instruction={"type": "prompt", "text": "Tell the caller you are transferring them."},
        ),
        fakes,
    )
    _run(runtime.start())

    assert events[-2:] == ["set_tools:0", "generate_reply"]
    assert fakes.transfers == ["+15555550101"]


# ---------------------------------------------------------------------------
# A raising injected callable degrades; it never strands the call mid-entry
# ---------------------------------------------------------------------------


def _one_node_flow() -> dict[str, Any]:
    return {
        "start_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "Hello there."},
            }
        ],
    }


def test_a_raising_set_tools_still_leaves_the_node_entered(caplog) -> None:
    fakes = Fakes()

    async def set_tools(tools: list[Any]) -> None:
        raise RuntimeError("session closed")

    fakes.set_tools = set_tools  # type: ignore[method-assign]
    runtime = _runtime(_one_node_flow(), fakes)
    with caplog.at_level(logging.ERROR, logger=FLOW_LOGGER):
        _run(runtime.start())

    # The node's line is still delivered: entry degraded, it did not abort.
    assert fakes.said == ["Hello there."]
    assert runtime.current_node_id == "n1"
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_a_raising_say_does_not_propagate_out_of_entry(caplog) -> None:
    fakes = Fakes()

    async def say(text: str) -> None:
        raise RuntimeError("no audio track")

    fakes.say = say  # type: ignore[method-assign]
    runtime = _runtime(_one_node_flow(), fakes)
    with caplog.at_level(logging.ERROR, logger=FLOW_LOGGER):
        _run(runtime.start())

    assert runtime.current_node_id == "n1"
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_a_raising_set_instructions_does_not_propagate_out_of_entry(caplog) -> None:
    fakes = Fakes()

    async def set_instructions(text: str) -> None:
        raise RuntimeError("agent detached")

    fakes.set_instructions = set_instructions  # type: ignore[method-assign]
    runtime = _runtime(_one_node_flow(), fakes)
    with caplog.at_level(logging.ERROR, logger=FLOW_LOGGER):
        _run(runtime.start())

    # Tools still installed and the line still spoken: no half-entered node.
    assert fakes.tools and fakes.said == ["Hello there."]
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


# ---------------------------------------------------------------------------
# press_digit
# ---------------------------------------------------------------------------


def _press_digit_flow(**node_extra: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": "dial",
        "type": "press_digit",
        "instruction": {"type": "prompt", "text": "Press 2 for the pharmacy line."},
        "edges": [
            {
                "id": "reached",
                "transition_condition": _prompt_condition("Menu reached"),
                "destination_node_id": "done",
            }
        ],
    }
    node.update(node_extra)
    return {
        "start_node_id": "dial",
        "start_speaker": "agent",
        "nodes": [
            node,
            {
                "id": "done",
                "type": "conversation",
                "instruction": {"type": "static_text", "text": "You're through."},
            },
        ],
    }


def test_press_digit_node_is_entered_like_a_speaking_node() -> None:
    """It carries a ``prompt`` instruction naming what to press, so the model
    needs the node's instructions and its DTMF tool installed — the node is
    NOT a silent router that transitions on entry."""
    fakes = Fakes()
    runtime = _runtime(_press_digit_flow(), fakes)
    _run(runtime.start())

    assert runtime.current_node_id == "dial"
    assert fakes.build_calls[-1][0] == "dial"
    assert "Press 2 for the pharmacy line." in fakes.instructions[-1]
    assert runtime.ended is False


def test_press_digit_node_opening_the_call_gets_a_model_turn() -> None:
    # Same rule as any other prompt-instruction node opening the call: without
    # a requested turn the call opens in silence.
    fakes = Fakes()
    runtime = _runtime(_press_digit_flow(), fakes)
    _run(runtime.start())
    assert fakes.generate_reply_calls == [None]


def test_press_digit_node_counts_as_a_routing_node() -> None:
    """It cannot hold a conversation — it presses digits and moves on — so it
    belongs with `function` / `extract_dynamic_variables` for `_dead_end`'s
    "stay put or end the call?" question, not with `conversation`.

    Like those two siblings this is a classification, not a reachable path
    today: `_dead_end` is only entered via `_follow_fallback`, which only
    `branch` and `transfer_call` nodes reach. Asserting the membership is what
    keeps the classification from silently flipping.
    """
    assert "press_digit" in _ROUTING_NODE_TYPES
    assert "conversation" not in _ROUTING_NODE_TYPES


def test_press_digit_node_with_a_dangling_edge_stays_put() -> None:
    """Its tool is what routes it, so an unusable edge leaves the call on the
    node rather than ending it — the same shape a `function` node has."""
    fakes = Fakes()
    flow = _press_digit_flow(edges=[], else_edge={"id": "dangling"})
    runtime = _runtime(flow, fakes)
    _run(runtime.start())
    _run(runtime.on_user_turn())

    assert fakes.ended == []
    assert runtime.ended is False
    assert runtime.current_node_id == "dial"
