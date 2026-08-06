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
from arhiteq_api.services import knowledge, simulation
from arhiteq_api.services.template_variables import CallVariables, resolve_template


class FakeModel:
    """Returns queued replies in order, recording the prompts it was given."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts: list[str] = []
        # Recorded alongside the prompts: how a call was sampled is part of the
        # harness's behaviour, not Gemini's.
        self.configs: list[dict] = []

    async def generate_content(self, *, model, contents, config):
        self.prompts.append(contents)
        self.configs.append(dict(config))
        if not self._replies:
            raise AssertionError(f"unexpected extra model call:\n{contents[:400]}")
        reply = self._replies.pop(0)
        return types.SimpleNamespace(text=reply if isinstance(reply, str) else json.dumps(reply))


def fake_client(replies):
    model = FakeModel(replies)
    client = types.SimpleNamespace(aio=types.SimpleNamespace(models=model))
    return client, model


def _flat(text: str) -> str:
    """Prompt text with its line wrapping collapsed, for asserting on phrases.

    Prompt paragraphs are wrapped to a fixed width, so a sentence an assertion
    cares about is usually split across lines — and where it splits moves
    whenever a word is edited or an interpolated list changes length.
    """
    return " ".join(text.split())


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


@pytest.mark.parametrize(
    "raw",
    [
        # A sentence after the answer.
        '{"action": "speak", "content": "hi"}\nLet me know if you need anything else.',
        # A second object the run has no use for.
        '{"action": "speak", "content": "hi"}\n{"action": "end_call"}',
        # Preamble and trailer at once.
        'Here you go: {"action": "speak", "content": "hi"} — hope that helps!',
        # Pretty-printed, which is where the trailing brace of the *first*
        # object is nowhere near the last brace in the text.
        '{\n "action": "speak",\n "content": "hi"\n}\n{"note": "extra"}',
    ],
)
def test_json_object_ignores_trailing_content(raw):
    """A stray sentence after the JSON must not cost the whole run.

    `json.loads` raises "Extra data" on all of these, and the reply is otherwise
    perfectly usable — three runs of a 98-case batch died this way.
    """
    assert simulation._json_object(raw)["content"] == "hi"


@pytest.mark.parametrize(
    "raw",
    [
        '[{"action": "speak", "content": "hi"}]',
        '```json\n[{"action": "speak", "content": "hi"}]\n```',
        '[{"action": "speak", "content": "hi"}, {"action": "end_call"}]',
    ],
)
def test_json_object_unwraps_a_listed_object(raw):
    """A single action wrapped in a list is still that action.

    Models reach for an array when asked for one object, and the reply is not
    ambiguous — the first object in it is the answer.
    """
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


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"type": "custom", "name": "a"}, None),
        ({"type": "custom", "name": "a", "speak_during_execution": False}, None),
        # execution_message_type "prompt" would have the live LLM phrase its own
        # filler; the worker approximates it with a generic line, and so do we.
        (
            {"type": "custom", "name": "a", "speak_during_execution": True},
            "One moment, let me check that.",
        ),
        (
            {
                "type": "custom",
                "name": "a",
                "speak_during_execution": True,
                "execution_message_type": "prompt",
                "execution_message_description": "say something warm",
            },
            "One moment, let me check that.",
        ),
        (
            {
                "type": "custom",
                "name": "a",
                "speak_during_execution": True,
                "execution_message_type": "static_text",
                "execution_message_description": "Let me look that up for you.",
            },
            "Let me look that up for you.",
        ),
        # static_text with nothing to say still falls back rather than going mute
        (
            {
                "type": "custom",
                "name": "a",
                "speak_during_execution": True,
                "execution_message_type": "static_text",
                "execution_message_description": "",
            },
            "One moment, let me check that.",
        ),
        # the worker only speaks for custom tools, whatever the flag says
        ({"type": "end_call", "speak_during_execution": True}, None),
    ],
)
def test_execution_filler_mirrors_the_worker(entry, expected):
    assert simulation.execution_filler(entry) == expected


@pytest.mark.anyio
async def test_a_speaking_tool_puts_its_filler_in_the_transcript(monkeypatch):
    """The filler is the platform speaking, so nothing else would record it."""
    sim, _ = make_simulator(
        monkeypatch,
        [],
        catalog=[
            {
                "name": "web_lookup",
                "type": "custom",
                "description": "",
                "parameters": {},
                "filler": "Let me check that for you, {{first_name}}.",
            },
            {
                "name": "log_mood",
                "type": "custom",
                "description": "",
                "parameters": {},
                "filler": None,
            },
        ],
        variables={"first_name": "Margaret"},
    )

    async def _result(tool, name, args):
        return json.dumps({"ok": True})

    monkeypatch.setattr(sim, "_tool_result", _result)

    await sim._invoke_tool({"tool_name": "web_lookup", "arguments": {"query": "weather"}})
    await sim._invoke_tool({"tool_name": "log_mood", "arguments": {}})

    turns = [(t["role"], t.get("content") or t.get("name")) for t in sim.transcript]
    # the filler is spoken before the call goes out, not after it returns
    assert turns[0] == ("agent", "Let me check that for you, Margaret.")
    assert [role for role, _ in turns[1:]] == [
        "tool_call_invocation",
        "tool_call_result",
        # the silent tool contributes no spoken turn
        "tool_call_invocation",
        "tool_call_result",
    ]


# ------------------------------------------------------------------ kb_lookup

KB_TOOL = {
    "name": "kb_lookup",
    "type": "kb_lookup",
    "description": "Look up a fact",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
}


def _kb_view(**overrides):
    return knowledge.KnowledgeBaseView(
        knowledge_base_id="know_1",
        knowledge_base_name="Company",
        sources=overrides.pop(
            "sources",
            [
                {
                    "type": "text",
                    "source_id": "src_1",
                    "title": "Pricing",
                    "content": "The plan costs $29 per month.",
                }
            ],
        ),
    )


async def test_kb_lookup_runs_for_real_instead_of_being_invented(monkeypatch):
    """The one tool the harness executes.

    A synthesized payload would be fabricated facts, so a criterion about
    answering from the knowledge base could only ever grade the harness.
    """
    # No queued replies: reaching the model at all would raise.
    sim, model = make_simulator(monkeypatch, [], catalog=[KB_TOOL], knowledge_bases=[_kb_view()])

    result = json.loads(
        await sim._tool_result(KB_TOOL, "kb_lookup", {"query": "what does the plan cost"})
    )

    assert result["results"] == [{"title": "Pricing", "content": "The plan costs $29 per month."}]
    assert model.prompts == [], "kb_lookup must not be synthesized by the model"


async def test_kb_lookup_miss_tells_the_agent_not_to_guess(monkeypatch):
    sim, _ = make_simulator(monkeypatch, [], catalog=[KB_TOOL], knowledge_bases=[_kb_view()])
    result = json.loads(await sim._tool_result(KB_TOOL, "kb_lookup", {"query": "zebra migration"}))
    assert result["results"] == []
    assert result["message"] == knowledge.NO_RESULTS_MESSAGE


async def test_kb_lookup_without_an_attached_base_is_a_miss(monkeypatch):
    sim, _ = make_simulator(monkeypatch, [], catalog=[KB_TOOL], knowledge_bases=[])
    result = json.loads(
        await sim._tool_result(KB_TOOL, "kb_lookup", {"query": "what does the plan cost"})
    )
    assert result["results"] == []


async def test_an_explicit_mock_still_overrides_kb_lookup(monkeypatch):
    """A case testing "the KB doesn't know" must be able to force that."""
    sim, _ = make_simulator(
        monkeypatch,
        [],
        catalog=[KB_TOOL],
        knowledge_bases=[_kb_view()],
        definition={
            "user_prompt": "You ask about pricing.",
            "metrics": [],
            "tool_mocks": [{"tool_name": "kb_lookup", "output": '{"results": []}'}],
        },
    )
    result = json.loads(
        await sim._tool_result(KB_TOOL, "kb_lookup", {"query": "what does the plan cost"})
    )
    assert result == {"results": []}


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


async def test_a_synthesized_tool_result_is_told_what_the_call_is_about(monkeypatch):
    """An invented payload is the one thing in a run that speaks as the backend,
    and the agent repeats it to the caller — so it is given the scenario, the
    case's variables and the call so far, and told not to contradict them.

    Without this, a memory lookup on a case about Jane's gardening comes back
    about a different person entirely, and the criterion that fails is the one
    about remembering the note.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Did you see my note about the hydrangeas?"},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"status": "queued"},  # the synthesized tool payload
            {"action": "speak", "content": "I did!"},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "Clara should remember your gardening note from yesterday.",
            "metrics": [],
        },
        variables=CallVariables(
            {"first_name": "Jane", "prior_conversation": "wanted to do some gardening"},
            call_id="call_sim1",
        ),
    )
    await sim.run()
    synthesis = _flat(model.prompts[2])
    assert "Clara should remember your gardening note from yesterday." in synthesis
    assert "- first_name: Jane" in synthesis
    assert "- prior_conversation: wanted to do some gardening" in synthesis
    # The call it is answering, and the turn that prompted it, are both context
    # the payload has to fit.
    assert "Tool call: schedule_callback" in synthesis
    assert "Did you see my note about the hydrangeas?" in synthesis
    # A call id tells an invented payload nothing; four empty dotted keys would
    # only invite the model to fill them in.
    assert "call.call_id" not in synthesis
    assert "call.from_number" not in synthesis


async def test_a_synthesized_tool_result_may_deny_what_the_caller_claims(monkeypatch):
    """Only the variables are the record; the scenario is the caller's account.

    Generation is asked for callers who give contradictory information, so a
    scenario that says to insist last month's payment went through is one the
    lookup has to be free to deny. A harness told to agree with the scenario
    would invent the payment, the agent would confirm it, and the criterion
    about telling the caller no payment is on file would fail on the harness.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "I definitely paid last month."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"status": "queued"},
            {"action": "speak", "content": "Let me take a look."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "You insist you already paid last month. You have no receipt.",
            "metrics": [],
        },
        variables=CallVariables({"first_name": "James", "last_payment": "none on file"}),
    )
    await sim.run()
    synthesis = _flat(model.prompts[2])
    assert "The part the caller is playing (their own account, not a record):" in synthesis
    assert "What the systems behind this call actually hold — the record:" in synthesis
    assert "- last_payment: none on file" in synthesis
    assert "a claim of theirs the state does not back is not something to write in" in synthesis


async def test_a_synthesized_tool_result_survives_a_case_with_no_state(monkeypatch):
    """No scenario and no variables is a formatting hazard, not a failure."""
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Call me back."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"status": "queued"},
            {"action": "speak", "content": "All set."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={"user_prompt": "", "metrics": []},
        variables=CallVariables({}),
    )
    transcript = await sim.run()
    assert json.loads(transcript[3]["content"]) == {"status": "queued"}
    assert "(nothing beyond the call so far)" in model.prompts[2]


async def test_the_simulated_caller_is_held_to_the_scenario(monkeypatch):
    """A caller who invents a motive grades the agent on a different case.

    A scenario about paying on day 7 of a trial opened instead with "trying to
    figure out if I want to keep the service going", the agent took the
    cancellation branch it was right to take, and both criteria — about the
    payment the call never reached — failed on the harness.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Hi Clara."},
            {"action": "speak", "content": "Hello!"},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "You are on day 7 of your trial. When asked about payment, "
            "insist on using your phone's keypad.",
            "metrics": [],
        },
    )
    await sim.run()
    caller = _flat(model.prompts[0])
    assert "do not invent a motive, a complaint or a request it does not give you" in caller
    assert 'a fact it frames as an answer ("when asked, …", "confirm that …") is yours' in caller


async def test_a_caller_with_no_scenario_is_not_told_to_invent_nothing(monkeypatch):
    """A case may carry no scenario at all — only missing criteria stop a run.

    Told a blank description is everything they know, and to hang up once the
    goal it does not state is out of reach, the caller walks off the call: an
    empty transcript fails every criterion on the harness.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Hi?"},
            {"action": "speak", "content": "Hello!"},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={"user_prompt": "   ", "metrics": []},
    )
    await sim.run()
    caller = _flat(model.prompts[0])
    assert "do not invent a motive" not in caller
    assert "You are role-playing a person on a phone call" in caller


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


@pytest.mark.parametrize("start_speaker", ["agent", "user"])
@pytest.mark.parametrize("begin_message", ["", "   "])
def test_greeting_note_is_silent_when_the_agent_authors_its_own_opening(
    begin_message, start_speaker
):
    """No greeting: the agent improvises its opener, which IS behaviour."""
    assert simulation._greeting_note(begin_message, start_speaker) == ""


def test_greeting_note_names_the_variables_the_first_line_is_built_from():
    """A variable-built greeting is steerable, so the note asks for it to be set.

    The scenario supplies the values the way the live caller-context lookup
    would; dropping the criterion is offered second, because a greeting that
    varies is worth covering.
    """
    # The note is wrapped to the prompt's width, so assert against it unwrapped
    # rather than pinning where the line breaks happen to land.
    note = _flat(simulation._greeting_note("Good {{time_of_day}} {{first_name}}!", "agent"))

    assert "spoken verbatim" in note
    assert "It reads {{time_of_day}}, {{first_name}}" in note
    assert "give them values that make it open the way this scenario needs" in note


def test_greeting_note_keeps_a_greeting_value_usable_everywhere_else():
    """A placeholder in the greeting is usually not the whole greeting.

    "Good morning {{first_name}}!" is the common shape, and `{{first_name}}` is
    read by the rest of the prompt and by tool arguments too. Told plainly to
    set the greeting, a model answers this shape by stuffing the whole line into
    `first_name` — which then renders in every other sentence that reads it, a
    fresh way for a case to fail on its own setup.
    """
    note = _flat(simulation._greeting_note("Good morning {{first_name}}!", "agent"))

    assert "values that still read correctly everywhere else the prompt uses them" in note


def test_greeting_note_forbids_grading_a_greeting_nothing_can_vary():
    """A fixed first line is a constant: no scenario can make it say otherwise."""
    note = _flat(simulation._greeting_note("Hi, it's Clara.", "agent"))

    assert "reads no variables" in note
    assert "Never write a criterion about the greeting" in note


def test_greeting_note_marks_a_first_line_the_caller_talks_over():
    """`start_speaker: "user"` never plays the greeting, but the prompt shows it.

    Passing this case over in silence would leave the one line that is purely
    dead text as the only one the model is shown and told nothing about.
    """
    note = _flat(simulation._greeting_note("Good morning {{first_name}}!", "user"))

    assert "is never spoken" in note
    assert "Do not write a criterion about the greeting" in note


def test_greeting_note_never_splits_a_variable_name_across_lines():
    """A name broken by the wrapper is one the model can copy back wrong.

    Placeholder names may hold hyphens and are arbitrarily long, so `textwrap`'s
    defaults would hyphen-break `{{subscription-tier-label}}` mid-name.
    """
    names = ["greeting-line", "caller-first-name", "subscription-tier-label", "x" * 70]
    note = simulation._greeting_note(" ".join(f"{{{{{n}}}}}" for n in names), "agent")

    for name in names:
        assert f"{{{{{name}}}}}" in note


async def test_generate_warns_that_a_greeting_is_not_the_agents_to_choose(monkeypatch):
    """The two traps that make a case fail on its own setup reach the model.

    Both come from one live failure: a scenario about an *inbound* paid
    subscriber that left the greeting variable at its outbound default, then
    graded the agent on a first line it never wrote and could not have changed.
    """
    client, model = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Last day",
                        "user_prompt": "You are on the last day of your trial.",
                        "metrics": ["The agent says the trial is ending"],
                        "dynamic_variables": {"is_last_day_of_trial": "true"},
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)

    await simulation.generate_test_cases(_gated_llm(), 1)

    sent = _flat(model.prompts[0])
    # The greeting is `begin_message`, not behaviour, and it is built from a
    # variable this prompt reads.
    assert "The agent's first line is not its own." in sent
    assert "It reads {{first_name}}" in sent
    assert "a criterion about the greeting grades those values rather than the agent" in sent
    # And an agent default is a value rather than an absence, so a scenario it
    # contradicts is graded in the state it was written to avoid.
    assert "it means *that* value" in sent


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


async def test_generate_moves_every_time_in_a_scenario_by_the_same_delta(monkeypatch):
    """The gaps between a scenario's times are the scenario.

    The generate prompt asks for the other times "relative to" the pin, so
    re-dating the pin alone would leave a case whose last dose was an hour
    before the call three years apart from it — and a branch reading the gap
    unreachable. Stale-but-agreeing is better than fresh-but-contradictory.
    """
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Dose window",
                        "user_prompt": "You took your pills an hour ago.",
                        "metrics": ["The agent logs the dose"],
                        "dynamic_variables": {
                            "current_time": "2023-10-27T08:15",
                            "last_dose_at": "2023-10-27 07:15",
                            "trial_ends_on": "2023-11-15",
                            "medications_today": "Lipitor at 08:00",
                        },
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    monkeypatch.setattr(simulation, "_today", lambda: date(2030, 1, 5))

    variables = (await simulation.generate_test_cases(_gated_llm(), 1))[0]["dynamic_variables"]

    assert variables["current_time"] == "2030-01-05T08:15:00"
    # An hour before the call, still an hour before the call.
    assert variables["last_dose_at"] == "2030-01-05T07:15:00"
    # 19 days out before, 19 days out after.
    assert variables["trial_ends_on"] == "2030-01-24"
    # Not a timestamp, and the pin keeps the time of day it lines up with.
    assert variables["medications_today"] == "Lipitor at 08:00"


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        # A bare time of day: what the prompt's emphasis on the window invites.
        ("08:15", "2030-01-05T08:15:00"),
        ("8:15", "2030-01-05T08:15:00"),
        # An offset is dropped, not converted: a pin means wall-clock, and
        # honouring this one would land the call at 19:00 the previous day.
        ("2023-10-27T02:00:00Z", "2030-01-05T02:00:00"),
        ("2023-10-27T08:15+02:00", "2030-01-05T08:15:00"),
    ],
)
async def test_generate_pins_the_shapes_a_model_actually_writes(monkeypatch, written, expected):
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

    assert cases[0]["dynamic_variables"]["current_time"] == expected


async def test_generate_leaves_other_times_alone_when_the_pin_is_not_one(monkeypatch):
    """No pin, no delta — so nothing else may be moved either."""
    client, _ = fake_client(
        [
            {
                "test_cases": [
                    {
                        "name": "Trial end",
                        "user_prompt": "Your trial is ending.",
                        "metrics": ["The agent offers to continue"],
                        "dynamic_variables": {"trial_ends_on": "2023-11-15"},
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    monkeypatch.setattr(simulation, "_today", lambda: date(2030, 1, 5))

    cases = await simulation.generate_test_cases(_gated_llm(), 1)

    assert cases[0]["dynamic_variables"]["trial_ends_on"] == "2023-11-15"


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


def test_the_stand_in_defaults_to_the_analysis_model():
    """Unset changes nothing: the knob exists to be pointed, not to move on its own.

    Which model plays the agent decides individual verdicts, and none of the
    ones measured dominates — so the default must preserve behaviour rather
    than pick a winner.
    """
    settings = get_settings()
    assert settings.simulation_agent_model is None


# ------------------------------------------------------- agent swap (routing)


SWAP_CATALOG = [
    {
        "name": "transfer_to_pharmacy",
        "type": "agent_swap",
        "agent_id": "agent_pharmacy",
        "description": "Hand the caller to the pharmacy specialist.",
        "parameters": {"type": "object", "properties": {}},
        "filler": None,
    },
    {
        "name": "end_call",
        "type": "end_call",
        "description": "Hang up",
        "parameters": {"type": "object", "properties": {}},
        "filler": None,
    },
]

PHARMACY_DESTINATION = {
    "agent_pharmacy": {
        "agent_name": "Pharmacy specialist",
        "general_prompt": "You are the pharmacy specialist for {{first_name}}.",
        "catalog": [
            {
                "name": "lookup_price",
                "type": "custom",
                "description": "Look up a drug price",
                "parameters": {"type": "object", "properties": {}},
                "filler": None,
            }
        ],
        "knowledge_bases": [],
        "timezone": "America/New_York",
    }
}


def test_tool_catalog_carries_the_swap_destination():
    """The model never chooses where a swap goes — the tool config does.

    The worker gives `agent_swap` an empty argument schema, so the destination
    has to travel on the catalog entry or the run cannot follow it.
    """
    llm = RetellLLM(
        llm_id="llm_x",
        workspace_id="ws",
        general_tools=[
            {"type": "agent_swap", "name": "to_pharmacy", "agent_id": "agent_p"},
            {"type": "custom", "name": "book"},
        ],
    )
    catalog = simulation.tool_catalog(llm)
    assert catalog[0]["agent_id"] == "agent_p"
    assert "agent_id" not in catalog[1]
    assert simulation.swap_agent_ids(llm) == ["agent_p"]


def test_swap_agent_ids_ignores_entries_without_a_destination():
    llm = RetellLLM(
        llm_id="llm_x",
        workspace_id="ws",
        general_tools=[
            {"type": "agent_swap", "name": "broken"},  # never configured
            {"type": "agent_swap", "name": "ok", "agent_id": "agent_a"},
            "not a dict",
        ],
    )
    assert simulation.swap_agent_ids(llm) == ["agent_a"]


async def test_a_swap_continues_the_call_under_the_new_prompt(monkeypatch):
    """The point of the whole feature: a handoff is one call, so one transcript.

    A router that swapped and then hung up would grade every specialist
    criterion as "the agent never did it".
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "tool_call", "tool_name": "transfer_to_pharmacy", "arguments": {}},
            {"action": "speak", "content": "Pharmacy here — which prescription?"},
            {},  # the caller says nothing more, which ends the run
        ],
        catalog=list(SWAP_CATALOG),
        swap_destinations=PHARMACY_DESTINATION,
        variables=CallVariables({"first_name": "Margaret"}),
        begin_message=None,
    )
    transcript = await sim.run()

    assert [item["role"] for item in transcript] == [
        "tool_call_invocation",
        "tool_call_result",
        "agent",
    ]
    assert json.loads(transcript[1]["content"]) == {"result": "now acting as agent agent_pharmacy"}
    # The specialist answered with the router's conversation already behind it.
    assert transcript[2]["content"] == "Pharmacy here — which prescription?"

    # The turn after the swap was prompted with the destination's prompt and
    # its tools — resolved against the call's variables, as a live swap is.
    after_swap = model.prompts[1]
    assert "You are the pharmacy specialist for Margaret." in after_swap
    # The catalog is the destination's, so the router's own tools are gone.
    # (The swap still shows in the history below it — that is the point.)
    assert "- lookup_price:" in after_swap
    assert "- transfer_to_pharmacy:" not in after_swap
    assert "- end_call:" not in after_swap


async def test_a_swap_adopts_the_destination_agents_timezone(monkeypatch):
    """Mirrors the worker: the destination's own time awareness takes over."""
    variables = CallVariables({}, default_timezone="America/Los_Angeles")
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "tool_call", "tool_name": "transfer_to_pharmacy", "arguments": {}},
            {"action": "speak", "content": "Pharmacy here."},
            {},
        ],
        catalog=list(SWAP_CATALOG),
        swap_destinations=PHARMACY_DESTINATION,
        variables=variables,
        begin_message=None,
    )
    await sim.run()
    assert variables["current_time_America/New_York"] == variables["current_time"]


async def test_an_unavailable_destination_leaves_the_run_on_the_current_agent(monkeypatch):
    """Unknown, cross-workspace or LLM-less destinations never load.

    A live call gets an error back from the tool and keeps talking as the agent
    it already was, so a run must not silently blank the prompt instead.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "tool_call", "tool_name": "transfer_to_pharmacy", "arguments": {}},
            {"action": "speak", "content": "Let me help you with that myself."},
            {"action": "hangup", "reason": "done"},
        ],
        catalog=list(SWAP_CATALOG),
        swap_destinations={},
        begin_message=None,
    )
    transcript = await sim.run()
    assert json.loads(transcript[1]["content"]) == {"error": "destination agent is not available"}
    assert "You are Clara, a scheduling assistant." in model.prompts[1]


async def test_the_wrap_up_turn_runs_as_the_agent_that_holds_the_call(monkeypatch):
    """A caller who hangs up on the specialist gets the specialist's closing work.

    The wrap-up turn exists so "the agent logs the outcome" is gradeable; after
    a swap it has to be the destination's prompt and tools that get that turn,
    or a specialist's logging tool is unreachable.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "tool_call", "tool_name": "transfer_to_pharmacy", "arguments": {}},
            {"action": "speak", "content": "Pharmacy here."},
            {"action": "hangup", "reason": "the caller hung up"},
            {"action": "tool_call", "tool_name": "lookup_price", "arguments": {}},
            {"result": "ok"},  # the synthesized tool payload
        ],
        catalog=list(SWAP_CATALOG),
        swap_destinations=PHARMACY_DESTINATION,
        begin_message=None,
    )
    await sim.run()
    assert "lookup_price" in model.prompts[-2]


# ------------------------------------------------------- scripted caller


async def test_a_scripted_caller_plays_its_lines_in_order(monkeypatch):
    """The scripted branch never reaches the user-simulator model.

    Only agent replies are queued — plus one for the wrap-up turn the hang-up
    earns. If the harness asked a model for a caller line, FakeModel would raise
    on the extra call, so the queue length is the assertion.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "How are you feeling?"},
            {"action": "speak", "content": "Glad to hear it."},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "",
            "metrics": [],
            "script": ["Morning, Clara.", "All fine here."],
        },
    )
    await sim.run()

    said = [item["content"] for item in sim.transcript if item["role"] == "user"]
    assert said == ["Morning, Clara.", "All fine here."]
    assert sim.ending == "the caller hung up"
    # The user-simulator prompt opens "You are role-playing a person…"; no
    # prompt sent to any model may contain it.
    assert not any("role-playing" in prompt for prompt in model.prompts)


async def test_an_exhausted_script_earns_the_agent_a_wrap_up_turn(monkeypatch):
    """A prompt that logs its disposition while hanging up is still graded."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Take care."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "",
            "metrics": [],
            "script": ["That's everything, bye."],
            # Mocked so the harness does not spend a model call inventing a result.
            "tool_mocks": [
                {
                    "tool_name": "schedule_callback",
                    "input_match_rule": {"type": "any"},
                    "output": '{"ok": true}',
                }
            ],
        },
    )
    await sim.run()

    roles = [item["role"] for item in sim.transcript]
    assert "tool_call_invocation" in roles
    # The wrap-up work lands after the agent's last spoken line, exactly as it
    # does on a live call.
    last_spoken = max(i for i, role in enumerate(roles) if role == "agent")
    first_wrap_up_call = roles.index("tool_call_invocation")
    assert first_wrap_up_call > last_spoken


async def test_an_unscripted_case_still_improvises(monkeypatch):
    """The existing path is untouched: no script means the model plays the caller."""
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Hello?"},
            {"action": "speak", "content": "How are you?"},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={"user_prompt": "You want a callback.", "metrics": []},
    )
    await sim.run()

    assert any("role-playing" in prompt for prompt in model.prompts)


async def test_a_script_of_one_line_still_hangs_up_rather_than_stalling(monkeypatch):
    sim, _ = make_simulator(
        monkeypatch,
        [{"action": "speak", "content": "Hello."}, {"action": "done"}],
        definition={"user_prompt": "", "metrics": [], "script": ["Hi."]},
    )
    await sim.run()
    assert sim.ending == "the caller hung up"


async def test_a_script_as_long_as_the_turn_cap_still_reaches_its_last_line(monkeypatch):
    """The cap is not the budget for a scripted case.

    A script of MAX_TURNS lines spent the whole budget saying them, so the loop
    fell out of `range` before the pass that hangs up: `ending` stayed at the
    turn-limit text, no wrap-up turn ran, and every `ends_with` assertion failed
    with nothing in the output saying why.
    """
    script = [f"line {i}" for i in range(simulation.MAX_TURNS)]
    sim, _ = make_simulator(
        monkeypatch,
        # One agent reply per line, plus the wrap-up turn the hang-up earns.
        [{"action": "speak", "content": "mm-hm"} for _ in script] + [{"action": "done"}],
        definition={"user_prompt": "", "metrics": [], "script": script},
    )
    await sim.run()

    said = [item["content"] for item in sim.transcript if item["role"] == "user"]
    assert said == script
    assert sim.ending == "the caller hung up"


async def test_a_script_longer_than_the_turn_cap_is_played_in_full(monkeypatch):
    script = [f"line {i}" for i in range(simulation.MAX_TURNS + 5)]
    sim, _ = make_simulator(
        monkeypatch,
        [{"action": "speak", "content": "mm-hm"} for _ in script] + [{"action": "done"}],
        definition={"user_prompt": "", "metrics": [], "script": script},
    )
    await sim.run()

    said = [item["content"] for item in sim.transcript if item["role"] == "user"]
    assert said == script
    assert sim.ending == "the caller hung up"


async def test_a_scripted_run_samples_greedily_and_pins_a_seed(monkeypatch):
    """Repeatability is the whole premise, so nothing in the run may resample.

    The agent's own turn ran at 0.3 and an unmocked tool's invented result at
    0.4 — both feed the transcript the assertions read, so the same case graded
    a different conversation on the next run however mechanical the grading was.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"status": "queued"},  # the invented tool result
            {"action": "speak", "content": "Booked."},
            {"action": "done"},  # the wrap-up turn
        ],
        definition={"user_prompt": "", "metrics": [], "script": ["Book me in."]},
    )
    await sim.run()

    assert len(model.configs) == 4
    assert [c["temperature"] for c in model.configs] == [0.0, 0.0, 0.0, 0.0]
    assert all(c.get("seed") is not None for c in model.configs)
    assert len({c["seed"] for c in model.configs}) == 1


async def test_an_improvising_run_is_sampled_exactly_as_it_always_was(monkeypatch):
    """The other half of the same fix: a case with no script is untouched."""
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Book me in."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"status": "queued"},
            {"action": "speak", "content": "Booked."},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={"user_prompt": "You want a callback.", "metrics": []},
    )
    await sim.run()

    # caller, agent, invented tool result, agent, caller, wrap-up.
    assert [c["temperature"] for c in model.configs] == [0.7, 0.3, 0.4, 0.3, 0.7, 0.0]
    assert not any("seed" in c for c in model.configs)


async def test_an_invocation_records_the_tool_type(monkeypatch):
    """Additive: the name alone cannot say whether a call ended the call."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Sure."},
            {"action": "tool_call", "tool_name": "end_call", "arguments": {}},
        ],
        definition={"user_prompt": "", "metrics": [], "script": ["Bye."]},
    )
    await sim.run()

    invocations = [t for t in sim.transcript if t["role"] == "tool_call_invocation"]
    assert [(t["name"], t["type"]) for t in invocations] == [("end_call", "end_call")]


async def test_an_invented_tool_records_an_empty_type(monkeypatch):
    """A tool the agent made up has no catalog entry, so no type to record."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "tool_call", "tool_name": "wire_money", "arguments": {}},
            {"action": "speak", "content": "Done."},
            {"action": "done"},
        ],
        definition={"user_prompt": "", "metrics": [], "script": ["Send it."]},
    )
    await sim.run()

    invocation = next(t for t in sim.transcript if t["role"] == "tool_call_invocation")
    assert invocation["type"] == ""


# ------------------------------------------------------------- grading


def test_combine_results_puts_metrics_before_assertions():
    judged = [{"metric": "m", "passed": True, "explanation": "j"}]
    asserted = [{"metric": "a", "passed": True, "explanation": "a"}]
    status, results, explanation = simulation.combine_results(judged, asserted)
    assert status == "pass"
    assert [r["metric"] for r in results] == ["m", "a"]
    assert "2 criteria passed" in explanation


def test_combine_results_fails_when_any_entry_failed():
    judged = [{"metric": "m", "passed": True, "explanation": "j"}]
    asserted = [{"metric": "a", "passed": False, "explanation": "nope"}]
    status, _results, explanation = simulation.combine_results(judged, asserted)
    assert status == "fail"
    assert "a" in explanation and "nope" in explanation


def test_combine_results_refuses_to_pass_a_run_that_graded_nothing():
    """`all([])` is True, so an empty combination used to come back a pass.

    `has_gradable_criteria` keeps such a case from ever running, but the
    invariant belongs here too rather than three hundred lines away — a green
    badge on an untested agent is exactly what `judge` refuses to produce.
    """
    with pytest.raises(ValueError):
        simulation.combine_results([], [])


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({"metrics": ["does the thing"]}, True),
        ({}, False),
        ({"metrics": []}, False),
        ({"metrics": ["   "]}, False),
        # Assertions are not the judge's business.
        ({"assertions": [{"tool_called": "x"}]}, False),
    ],
)
def test_has_metrics_is_the_one_judged_criteria_predicate(snapshot, expected):
    assert simulation._has_metrics(snapshot) is expected


def test_combine_results_handles_assertions_only():
    status, results, _ = simulation.combine_results(
        [], [{"metric": "a", "passed": True, "explanation": "ok"}]
    )
    assert status == "pass"
    assert len(results) == 1


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({"metrics": ["does the thing"]}, True),
        ({"assertions": [{"tool_called": "log_mood"}]}, True),
        ({"metrics": ["x"], "assertions": [{"tool_called": "y"}]}, True),
        ({}, False),
        ({"metrics": [], "assertions": []}, False),
        # Whitespace-only criteria have never counted as gradable.
        ({"metrics": ["   "]}, False),
        # …but a whitespace-only metric alongside a real assertion does.
        ({"metrics": ["  "], "assertions": [{"ends_with": "end_call"}]}, True),
    ],
)
def test_has_gradable_criteria(snapshot, expected):
    assert simulation.has_gradable_criteria(snapshot) is expected
