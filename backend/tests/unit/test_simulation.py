"""The simulation engine itself: transcript shape, tool mocking, judging.

The model is replaced by a scripted fake, so these assert the harness's own
behaviour (what it feeds back, when it stops, how it grades) rather than
anything about Gemini.
"""

import json
import types
from datetime import date

import pytest

from arhiteq_api.config import get_settings
from arhiteq_api.models import RetellLLM
from arhiteq_api.services import simulation
from arhiteq_api.services.template_variables import CallVariables, resolve_template


class FakeModel:
    """Returns queued replies in order, recording the prompts it was given."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def generate_content(self, *, model, contents, config):
        self.prompts.append(contents)
        if not self._replies:
            raise AssertionError(f"unexpected extra model call:\n{contents[:400]}")
        reply = self._replies.pop(0)
        return types.SimpleNamespace(text=reply if isinstance(reply, str) else json.dumps(reply))


def fake_client(replies):
    model = FakeModel(replies)
    client = types.SimpleNamespace(aio=types.SimpleNamespace(models=model))
    return client, model


def make_simulator(monkeypatch, replies, *, definition=None, **kwargs):
    client, model = fake_client(replies)
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    sim = simulation._Simulator(
        settings=get_settings(),
        general_prompt="You are Clara, a scheduling assistant.",
        catalog=kwargs.pop(
            "catalog",
            [
                {
                    "name": "schedule_callback",
                    "type": "custom",
                    "description": "Schedule a callback",
                    "parameters": {"type": "object", "properties": {}},
                },
                {
                    "name": "end_call",
                    "type": "end_call",
                    "description": "Hang up",
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
        ),
        definition=definition or {"user_prompt": "You want a callback.", "metrics": []},
        agent_model="gemini-test",
        begin_message=kwargs.pop("begin_message", "Hi, this is Clara."),
        start_speaker=kwargs.pop("start_speaker", "agent"),
        **kwargs,
    )
    return sim, model


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize(
    "raw",
    [
        '{"action": "speak", "content": "hi"}',
        '```json\n{"action": "speak", "content": "hi"}\n```',
        'Sure! {"action": "speak", "content": "hi"}',
    ],
)
def test_json_object_tolerates_fences_and_preamble(raw):
    assert simulation._json_object(raw)["content"] == "hi"


@pytest.mark.parametrize("raw", [None, "", "no json here", "[1, 2]"])
def test_json_object_rejects_non_objects(raw):
    with pytest.raises((ValueError, TypeError)):
        simulation._json_object(raw)


# --------------------------------------------------------------- tool mocks


ANY_MOCK = {"tool_name": "book", "input_match_rule": {"type": "any"}, "output": "{}"}
PARTIAL_MOCK = {
    "tool_name": "book",
    "input_match_rule": {"type": "partial_match", "args": {"day": "monday"}},
    "output": '{"booked": true}',
}


def test_match_tool_mock_any_matches_every_call():
    assert simulation.match_tool_mock([ANY_MOCK], "book", {"day": "friday"}) is ANY_MOCK


def test_match_tool_mock_partial_ignores_extra_arguments():
    hit = simulation.match_tool_mock([PARTIAL_MOCK], "book", {"day": "monday", "hour": 9})
    assert hit is PARTIAL_MOCK


def test_match_tool_mock_partial_requires_every_listed_argument():
    assert simulation.match_tool_mock([PARTIAL_MOCK], "book", {"day": "tuesday"}) is None
    assert simulation.match_tool_mock([PARTIAL_MOCK], "book", {}) is None


def test_match_tool_mock_ignores_other_tools_and_unknown_rules():
    assert simulation.match_tool_mock([ANY_MOCK], "cancel", {}) is None
    typo = {"tool_name": "book", "input_match_rule": {"type": "partail_match"}, "output": "{}"}
    assert simulation.match_tool_mock([typo], "book", {}) is None


def test_tool_catalog_reads_the_llm_tool_entries():
    llm = RetellLLM(
        llm_id="llm_x",
        workspace_id="ws",
        general_tools=[
            {"type": "custom", "name": "book", "description": "Book", "parameters": {"a": 1}},
            {"type": "end_call"},  # unnamed: falls back to its type
            "not a dict",
        ],
    )
    catalog = simulation.tool_catalog(llm)
    assert [t["name"] for t in catalog] == ["book", "end_call"]
    assert catalog[0]["parameters"] == {"a": 1}
    assert catalog[1]["parameters"] == {"type": "object", "properties": {}}
    assert simulation.tool_catalog(None) == []


# ------------------------------------------------------------------ the run


async def test_run_uses_the_begin_message_and_ends_on_user_hangup(monkeypatch):
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "I'd like a callback."},
            {"action": "speak", "content": "Sure, when suits you?"},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},  # the wrap-up turn: nothing left to log
        ],
    )
    transcript = await sim.run()
    assert [(t["role"], t["content"]) for t in transcript] == [
        ("agent", "Hi, this is Clara."),
        ("user", "I'd like a callback."),
        ("agent", "Sure, when suits you?"),
    ]
    # The greeting was replayed verbatim, not generated.
    assert len(model.prompts) == 4
    assert sim.ending == "the caller hung up"


async def test_run_feeds_the_mocked_output_back_without_calling_a_tool(monkeypatch):
    definition = {
        "user_prompt": "You want a callback.",
        "metrics": [],
        "tool_mocks": [
            {
                "tool_name": "schedule_callback",
                "input_match_rule": {"type": "any"},
                "output": '{"confirmation": "CB-42"}',
            }
        ],
    }
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Call me at nine."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {"hour": 9}},
            {"action": "speak", "content": "Booked — reference CB-42."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition=definition,
    )
    transcript = await sim.run()
    roles = [t["role"] for t in transcript]
    assert roles == ["agent", "user", "tool_call_invocation", "tool_call_result", "agent"]
    invocation, result = transcript[2], transcript[3]
    assert json.loads(invocation["arguments"]) == {"hour": 9}
    assert result["content"] == '{"confirmation": "CB-42"}'
    assert invocation["tool_call_id"] == result["tool_call_id"]
    # A mocked tool must not cost an extra model call to invent a result.
    assert len(model.prompts) == 5
    # The result is visible to the next turn's prompt.
    assert "CB-42" in model.prompts[3]


async def test_one_utterance_can_draw_several_tool_calls_before_the_agent_speaks(monkeypatch):
    """A caller who reports two things in one breath gets both of them logged.

    A live model emits both calls for the single utterance that earned them;
    this harness asks for one action at a time, so the agent has to be told its
    turn is not over until it speaks — otherwise it answers the caller after the
    first result and the criterion about the second tool fails on the harness.
    """
    tools = ("log_mood", "log_medication_taken")
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "I'm good, and I took my morning pills."},
            {"action": "tool_call", "tool_name": "log_mood", "arguments": {"score": 5}},
            {"action": "tool_call", "tool_name": "log_medication_taken", "arguments": {"ok": True}},
            {"action": "speak", "content": "Lovely — I've noted both."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "You feel good and took your medication.",
            "metrics": [],
            "tool_mocks": [
                {"tool_name": name, "input_match_rule": {"type": "any"}, "output": "{}"}
                for name in tools
            ],
        },
        catalog=[
            {
                "name": name,
                "type": "custom",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            }
            for name in tools
        ],
    )
    transcript = await sim.run()
    assert [t["name"] for t in transcript if t["role"] == "tool_call_invocation"] == list(tools)
    assert transcript[-1] == {"role": "agent", "content": "Lovely — I've noted both."}
    # Chaining is asked for in the agent's own prompt, not left to the model —
    # including the one call it must not chain behind, since a turn that reaches
    # end_call never comes back to say the line it was saving for afterwards.
    assert "turn ends only when you speak" in model.prompts[1]
    assert "say what you have to say before it" in model.prompts[1]


async def test_a_farewell_saved_until_after_end_call_would_never_be_spoken(monkeypatch):
    """Why the chaining rule carves out the tools that take the line down."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "No, that's all."},
            {"action": "tool_call", "tool_name": "end_call", "arguments": {}},
        ],
    )
    transcript = await sim.run()
    assert [t["role"] for t in transcript][-2:] == ["tool_call_invocation", "tool_call_result"]
    assert sim.ending == "the agent ended it"


async def test_unmocked_tool_result_is_synthesized(monkeypatch):
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Call me back."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"status": "queued"},  # the synthesized tool payload
            {"action": "speak", "content": "All set."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
    )
    transcript = await sim.run()
    assert json.loads(transcript[3]["content"]) == {"status": "queued"}


async def test_unknown_tool_is_reported_back_as_an_error(monkeypatch):
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Hello?"},
            {"action": "tool_call", "tool_name": "wire_money", "arguments": {}},
            {"action": "speak", "content": "Sorry, I can't do that."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
    )
    transcript = await sim.run()
    assert json.loads(transcript[3]["content"]) == {"error": "unknown tool wire_money"}


async def test_terminal_tool_ends_the_call(monkeypatch):
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "That's all, bye."},
            {"action": "tool_call", "tool_name": "end_call", "arguments": {}},
        ],
    )
    transcript = await sim.run()
    assert transcript[-1]["role"] == "tool_call_result"
    assert transcript[-1]["name"] == "end_call"
    assert len(model.prompts) == 2  # nothing runs after the hang-up


async def test_start_speaker_user_lets_the_caller_open(monkeypatch):
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Hello? Is anyone there?"},
            {"action": "speak", "content": "Yes, hi!"},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        start_speaker="user",
    )
    transcript = await sim.run()
    assert transcript[0] == {"role": "user", "content": "Hello? Is anyone there?"}


async def test_tool_call_loop_is_broken_after_the_cap(monkeypatch):
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Book it."},
            *(
                [{"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}}, "{}"]
                * simulation.MAX_TOOL_CALLS_PER_TURN
            ),
            {"action": "hangup", "reason": "gave up"},
            {"action": "done"},
        ],
    )
    transcript = await sim.run()
    tool_calls = [t for t in transcript if t["role"] == "tool_call_invocation"]
    assert len(tool_calls) == simulation.MAX_TOOL_CALLS_PER_TURN
    assert "kept calling tools" in transcript[-1]["content"]


async def test_a_hangup_still_lets_the_agent_finish_its_wrap_up_calls(monkeypatch):
    """The bug this exists for: a prompt that says *speak, then log, then hang
    up* could never satisfy "the agent logs the outcome". The agent's turn ends
    when it speaks, and the caller hanging up on the goodbye used to end the
    run — so whether the criterion passed depended on whether the simulated
    user answered the goodbye or hung up on it."""
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Please cancel my subscription."},
            {"action": "speak", "content": "Of course. I'll stop the calls today. Take care."},
            {"action": "hangup", "reason": "goal met"},
            # Wrap-up: the disposition logging a live call does while hanging up.
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            "{}",
            {"action": "tool_call", "tool_name": "end_call", "arguments": {}},
        ],
    )
    transcript = await sim.run()
    assert [t["role"] for t in transcript[-4:]] == [
        "tool_call_invocation",
        "tool_call_result",
        "tool_call_invocation",
        "tool_call_result",
    ]
    assert transcript[-1]["name"] == "end_call"
    # Nothing was spoken into a dead line, and the terminal tool stopped the turn.
    assert transcript[2]["content"].endswith("Take care.")
    assert "do NOT speak" in model.prompts[3]


async def test_a_call_the_agent_never_spoke_on_gets_no_wrap_up_turn(monkeypatch):
    """Nothing to finish: a free tool-only turn would let "the agent calls X"
    pass for a call in which the agent did nothing at all."""
    sim, model = make_simulator(
        monkeypatch,
        [{"action": "hangup", "reason": "wrong number"}],
        start_speaker="user",
    )
    transcript = await sim.run()
    assert transcript == []
    assert len(model.prompts) == 1


async def test_an_empty_user_reply_is_not_reported_as_a_hangup(monkeypatch):
    """A harness glitch must not reach the judge dressed as a scenario fact."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Hello?"},
            {"action": "speak", "content": "Hi there."},
            {"action": "speak", "content": "   "},  # the simulator produced nothing
        ],
    )
    await sim.run()
    assert sim.ending == "the harness got no reply from the simulated caller"


async def test_a_failed_wrap_up_turn_still_leaves_a_gradeable_run(monkeypatch):
    """The conversation is already complete; one bad extra model call must not
    turn its verdicts into an `error` row."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Cancel my subscription, please."},
            {"action": "speak", "content": "Of course. Take care."},
            {"action": "hangup", "reason": "goal met"},
            "not json at all",  # the wrap-up call falls over
        ],
    )
    transcript = await sim.run()
    assert [t["content"] for t in transcript] == [
        "Hi, this is Clara.",
        "Cancel my subscription, please.",
        "Of course. Take care.",
    ]
    assert sim.ending == "the caller hung up"


async def test_an_agent_that_hangs_up_itself_gets_no_wrap_up_turn(monkeypatch):
    """It already ran what it meant to run — a second bite would let an agent
    that forgot the disposition log look like one that remembered."""
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "That's all, bye."},
            {"action": "tool_call", "tool_name": "end_call", "arguments": {}},
        ],
    )
    await sim.run()
    assert len(model.prompts) == 2
    assert sim.ending == "the agent ended it"


async def test_tool_arguments_resolve_the_call_scoped_placeholders(monkeypatch):
    """A prompt that says to pass `retell_call_id={{call.call_id}}` must not put
    the literal placeholder in the transcript — a live call resolves it."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Book it."},
            {
                "action": "tool_call",
                "tool_name": "schedule_callback",
                "arguments": {"retell_call_id": "{{call.call_id}}", "who": "{{first_name}}"},
            },
            "{}",
            {"action": "speak", "content": "Done."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        variables=CallVariables({"first_name": "Alice"}, call_id="call_sim1"),
    )
    transcript = await sim.run()
    assert json.loads(transcript[2]["arguments"]) == {
        "retell_call_id": "call_sim1",
        "who": "Alice",
    }


async def test_the_judge_is_told_how_the_call_ended(monkeypatch):
    definition = {"user_prompt": "…", "metrics": ["Agent logs the outcome"]}
    sim, model = make_simulator(
        monkeypatch,
        [{"results": [{"metric": "Agent logs the outcome", "passed": True, "explanation": "did"}]}],
        definition=definition,
    )
    sim.ending = "the caller hung up"
    await sim.judge()
    assert "How the call ended: the caller hung up." in model.prompts[0]


async def test_a_mid_call_failure_keeps_the_turns_that_happened(monkeypatch):
    """How far the call got is the most useful thing about a failed run."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "I'd like a callback."},
            {"action": "speak", "content": "Of course — when?"},
            "not json at all",  # the model falls over on the next user turn
        ],
    )
    with pytest.raises((ValueError, TypeError)):
        await sim.run()
    # The simulator still holds everything said before the failure, which is
    # what _run_one persists.
    assert [t["content"] for t in sim.transcript] == [
        "Hi, this is Clara.",
        "I'd like a callback.",
        "Of course — when?",
    ]


async def test_run_stops_at_the_turn_cap(monkeypatch):
    chatter = [
        {"action": "speak", "content": "and another thing"},
        {"action": "speak", "content": "I see"},
    ] * simulation.MAX_TURNS
    sim, model = make_simulator(monkeypatch, chatter)
    transcript = await sim.run()
    # greeting + MAX_TURNS user/agent pairs, and nothing said after the cap.
    assert len(transcript) == 1 + 2 * simulation.MAX_TURNS
    # The judge is told, so a criterion the call never reached is not read as
    # the agent declining to do it. A call cut off mid-conversation gets no
    # wrap-up turn either: it never reached an ending to wrap up.
    assert sim.ending == "the harness hit its turn limit before the call ended"
    assert len(model.prompts) == 2 * simulation.MAX_TURNS


# ------------------------------------------------------------------ judging


async def test_judge_passes_only_when_every_metric_passes(monkeypatch):
    definition = {
        "user_prompt": "You want a callback.",
        "metrics": ["Agent books the callback", "Agent confirms the time"],
    }
    sim, _ = make_simulator(
        monkeypatch,
        [
            {
                "results": [
                    {"metric": "Agent books the callback", "passed": True, "explanation": "did"},
                    {"metric": "Agent confirms the time", "passed": False, "explanation": "no"},
                ]
            }
        ],
        definition=definition,
    )
    status, results = await sim.judge()
    assert status == "fail"
    assert [r["passed"] for r in results] == [True, False]
    assert "1 of 2 criteria failed" in simulation._explain(status, results)


async def test_judge_counts_an_ungraded_metric_as_a_failure(monkeypatch):
    definition = {"user_prompt": "…", "metrics": ["Agent books the callback", "Agent is brief"]}
    sim, _ = make_simulator(
        monkeypatch,
        [{"results": [{"metric": "Agent books the callback", "passed": True, "explanation": ""}]}],
        definition=definition,
    )
    status, results = await sim.judge()
    assert status == "fail"
    assert results[1] == {
        "metric": "Agent is brief",
        "passed": False,
        "explanation": "The judge returned no verdict for this criterion.",
    }


async def test_judge_refuses_to_pass_a_case_with_no_criteria(monkeypatch):
    """A run that graded nothing must not show a green badge."""
    sim, model = make_simulator(monkeypatch, [])
    with pytest.raises(ValueError, match="no success criteria"):
        await sim.judge()
    assert model.prompts == []


@pytest.mark.parametrize(
    "returned",
    [
        "1. Agent books the callback",  # judge echoed the list number
        "  Agent   books the callback  ",  # re-wrapped whitespace
        "agent books the callback.",  # re-cased, trailing period
    ],
)
async def test_judge_matches_verdicts_despite_cosmetic_rewording(monkeypatch, returned):
    """A reworded criterion must not turn a clean run into a hard FAIL."""
    definition = {"user_prompt": "…", "metrics": ["Agent books the callback"]}
    sim, _ = make_simulator(
        monkeypatch,
        [{"results": [{"metric": returned, "passed": True, "explanation": "it did"}]}],
        definition=definition,
    )
    status, results = await sim.judge()
    assert status == "pass"
    # The criterion is reported as the operator wrote it, not as echoed back.
    assert results[0]["metric"] == "Agent books the callback"


async def test_judge_falls_back_to_order_when_criteria_are_rewritten(monkeypatch):
    definition = {"user_prompt": "…", "metrics": ["Books the callback", "Confirms the time"]}
    sim, _ = make_simulator(
        monkeypatch,
        [
            {
                "results": [
                    {"metric": "It scheduled a call back", "passed": True, "explanation": "a"},
                    {"metric": "It repeated the slot", "passed": False, "explanation": "b"},
                ]
            }
        ],
        definition=definition,
    )
    status, results = await sim.judge()
    assert status == "fail"
    assert [(r["metric"], r["passed"]) for r in results] == [
        ("Books the callback", True),
        ("Confirms the time", False),
    ]


async def test_judge_does_not_guess_when_the_verdict_count_differs(monkeypatch):
    """Positional fallback is only safe when the counts line up."""
    definition = {"user_prompt": "…", "metrics": ["Books the callback", "Confirms the time"]}
    sim, _ = make_simulator(
        monkeypatch,
        [{"results": [{"metric": "something else", "passed": True, "explanation": ""}]}],
        definition=definition,
    )
    status, results = await sim.judge()
    assert status == "fail"
    assert all(not r["passed"] for r in results)
    assert "no verdict" in results[0]["explanation"]


# --------------------------------------------------------------- generation


def _llm_with_tools():
    return RetellLLM(
        llm_id="llm_x",
        workspace_id="ws",
        general_prompt="You are Clara.",
        begin_message="Hi!",
        general_tools=[{"type": "custom", "name": "schedule_callback", "description": "Book"}],
    )


async def test_generate_keeps_only_mocks_for_tools_the_agent_has(monkeypatch):
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Happy path",
                        "user_prompt": "You want a callback.",
                        "metrics": ["Agent books it", "  "],
                        "tool_mocks": [
                            {"tool_name": "schedule_callback", "output": '{"ok":true}'},
                            {"tool_name": "wire_money", "output": "{}"},
                        ],
                    },
                    {"name": "No prompt", "user_prompt": "   ", "metrics": []},
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)

    cases = await simulation.generate_test_cases(_llm_with_tools(), 4)
    assert len(cases) == 1  # the prompt-less draft is dropped
    case = cases[0]
    assert case["metrics"] == ["Agent books it"]  # blank criterion dropped
    assert [m["tool_name"] for m in case["tool_mocks"]] == ["schedule_callback"]
    assert case["tool_mocks"][0]["input_match_rule"] == {"type": "any"}


async def test_generate_requires_credentials(monkeypatch):
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: False)
    with pytest.raises(RuntimeError, match="credentials"):
        await simulation.generate_test_cases(_llm_with_tools(), 2)


async def test_generate_drops_drafts_with_no_criteria(monkeypatch):
    """An ungradeable case must never reach the operator's suite."""
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {"name": "No criteria", "user_prompt": "You want a callback.", "metrics": []},
                    {
                        "name": "Usable",
                        "user_prompt": "You want a callback.",
                        "metrics": ["Agent books it"],
                    },
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    cases = await simulation.generate_test_cases(_llm_with_tools(), 4)
    assert [c["name"] for c in cases] == ["Usable"]


async def test_generate_raises_when_nothing_usable_comes_back(monkeypatch):
    client, _ = fake_client([{"test_cases": []}])
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    with pytest.raises(RuntimeError, match="no usable test cases"):
        await simulation.generate_test_cases(_llm_with_tools(), 2)


# ------------------------------------------------- generation: variable setup


def _gated_llm():
    """An agent whose interesting branch is gated on a dynamic variable."""
    return RetellLLM(
        llm_id="llm_gate",
        workspace_id="ws",
        general_prompt=(
            'If {{is_last_day_of_trial}} = "true", say the free week is ending.\n'
            "Call log_mood(phone={{phone}}).\n"
            "The time is {{current_time_{{user_timezone}}}}."
        ),
        begin_message="Good morning {{first_name}}!",
        default_dynamic_variables={"first_name": "friend"},
        general_tools=[{"type": "custom", "name": "log_mood", "description": "Log"}],
    )


async def test_generate_tells_the_model_which_variables_the_prompt_reads(monkeypatch):
    client, model = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Last day",
                        "user_prompt": "You are on the last day of your trial.",
                        "metrics": ["The agent says the trial is ending"],
                        "dynamic_variables": {"is_last_day_of_trial": "true", "phone": "+15551234"},
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)

    cases = await simulation.generate_test_cases(_gated_llm(), 1)

    sent = model.prompts[0]
    # Every name the prompt reads is offered, including the greeting's and the
    # inner name of the nested time key — not the composed key itself.
    assert "- {{is_last_day_of_trial}}" in sent
    assert "- {{phone}}" in sent
    assert "- {{user_timezone}}" in sent
    assert "{{current_time_{{user_timezone}}}}" not in sent.split("Dynamic variables")[1]
    # A name with an agent default is shown as already having a value.
    assert '- {{first_name}} (agent default: "friend")' in sent
    assert cases[0]["dynamic_variables"] == {"is_last_day_of_trial": "true", "phone": "+15551234"}


async def test_generate_offers_the_clock_to_a_prompt_that_reads_the_time(monkeypatch):
    """A time-gated prompt is told `current_time` is settable.

    The variables block otherwise lists only names `prompt_variables` reports,
    and a prompt asking the time as `{{current_time_{{user_timezone}}}}` reports
    the inner name alone — so the instruction to pin the clock would be naming a
    key the same bullet forbids inventing, for exactly the prompt shape that
    needs the pin most.
    """
    client, model = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Morning dose",
                        "user_prompt": "You took your pills.",
                        "metrics": ["The agent logs the dose"],
                        "dynamic_variables": {"current_time": "2026-07-27T08:15"},
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    monkeypatch.setattr(simulation, "_today", lambda: date(2026, 7, 27))

    cases = await simulation.generate_test_cases(_gated_llm(), 1)

    assert "- {{current_time}} (settable: pins the clock" in model.prompts[0]
    assert cases[0]["dynamic_variables"]["current_time"] == "2026-07-27T08:15:00"


@pytest.mark.parametrize(
    ("written", "expected_time"),
    [
        ("2023-10-27T08:15", "08:15:00"),  # a model-invented year, three years stale
        ("2026-07-27 19:30:00", "19:30:00"),
    ],
)
async def test_generate_moves_a_pinned_clock_onto_today(monkeypatch, written, expected_time):
    """The time of day is the scenario's; the date is the harness's.

    A stale date costs the dose window nothing but tests any date-sensitive
    branch — a trial ending, {{current_calendar}} — years from where it lives.
    """
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Morning dose",
                        "user_prompt": "You took your pills.",
                        "metrics": ["The agent logs the dose"],
                        "dynamic_variables": {"current_time": written},
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    monkeypatch.setattr(simulation, "_today", lambda: date(2030, 1, 5))

    cases = await simulation.generate_test_cases(_gated_llm(), 1)

    assert cases[0]["dynamic_variables"]["current_time"] == f"2030-01-05T{expected_time}"


async def test_generate_leaves_a_current_time_that_is_not_a_clock_alone(monkeypatch):
    """Anchoring follows the pin: prose is an ordinary variable, not a date."""
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Evening chat",
                        "user_prompt": "You are winding down.",
                        "metrics": ["The agent says goodnight"],
                        "dynamic_variables": {"current_time": "Tuesday at 7 PM"},
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)

    cases = await simulation.generate_test_cases(_gated_llm(), 1)

    assert cases[0]["dynamic_variables"]["current_time"] == "Tuesday at 7 PM"


async def test_generate_tells_the_model_todays_date(monkeypatch):
    client, model = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Morning dose",
                        "user_prompt": "You took your pills.",
                        "metrics": ["The agent logs the dose"],
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    monkeypatch.setattr(simulation, "_today", lambda: date(2030, 1, 5))

    await simulation.generate_test_cases(_gated_llm(), 1)

    assert '"2030-01-05T<HH:MM>"' in model.prompts[0]


async def test_generate_does_not_offer_the_clock_to_a_prompt_that_ignores_time(monkeypatch):
    client, model = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Plain case",
                        "user_prompt": "You want a callback.",
                        "metrics": ["The agent books it"],
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    llm = RetellLLM(
        llm_id="llm_plain",
        workspace_id="ws",
        general_prompt="Book a callback for {{first_name}}.",
    )

    await simulation.generate_test_cases(llm, 1)

    assert "settable: pins the clock" not in model.prompts[0]


@pytest.mark.parametrize("returned", [None, [], "true", {}])
async def test_generate_defaults_variables_to_an_empty_set(monkeypatch, returned):
    """A draft that names no variables is still usable — just unparameterized."""
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Plain",
                        "user_prompt": "You want a callback.",
                        "metrics": ["Agent books it"],
                        "dynamic_variables": returned,
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    cases = await simulation.generate_test_cases(_gated_llm(), 1)
    assert cases[0]["dynamic_variables"] == {}


async def test_generate_coerces_variable_values_the_way_a_prompt_reads_them(monkeypatch):
    """A JSON boolean must land as `"true"`, not Python's `"True"`.

    The prompt this feature exists for branches on `= "true"`; storing the
    model's `true` as `"True"` would leave that branch shut and reintroduce
    the failure the variables are meant to remove.
    """
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Typed",
                        "user_prompt": "You want a callback.",
                        "metrics": ["Agent books it"],
                        "dynamic_variables": {
                            "is_last_day_of_trial": True,
                            "is_day_1": False,
                            "prior_conversation": None,
                            "  ": "dropped — blank key",
                            "n": 7,
                        },
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    cases = await simulation.generate_test_cases(_gated_llm(), 1)
    assert cases[0]["dynamic_variables"] == {
        "is_last_day_of_trial": "true",
        "is_day_1": "false",
        # null is "no value", not the literal word None in a tool argument.
        "prior_conversation": "",
        "n": "7",
    }


def test_variable_value_renders_a_flag_the_prompt_can_match():
    """The coercion, end to end: the branch a `true` flag gates actually fires."""
    variables = {"is_last_day_of_trial": simulation._variable_value(True)}
    rendered = resolve_template('If {{is_last_day_of_trial}} = "true", say goodbye.', variables)
    assert rendered == 'If true = "true", say goodbye.'
