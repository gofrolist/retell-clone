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

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.flow import FlowGraph
from arhiteq_worker.flow_runtime import MAX_AUTOMATIC_TRANSITIONS, FlowRuntime

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

    async def say(self, text: str) -> None:
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


def test_a_branch_with_no_transition_at_all_stays_put_and_logs_an_error(caplog) -> None:
    branch = {"id": "b1", "type": "branch"}
    fakes = Fakes()
    runtime = _runtime(_branch_flow(branch), fakes)
    with caplog.at_level(logging.ERROR, logger=FLOW_LOGGER):
        _run(runtime.start())

    assert runtime.current_node_id == "b1"
    assert fakes.classify_calls == []
    assert any(record.levelno == logging.ERROR for record in caplog.records)


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
    # A static_text line is said verbatim; no model turn is requested for it.
    assert fakes.generate_reply_calls == []


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
    # No say() for the prompt line -- it was requested as a model turn instead.
    assert fakes.said == ["Anything else?"]
    assert fakes.generate_reply_calls == [None]


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
