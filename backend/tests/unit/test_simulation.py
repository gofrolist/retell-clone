"""The simulation engine itself: transcript shape, tool mocking, judging.

The model is replaced by a scripted fake, so these assert the harness's own
behaviour (what it feeds back, when it stops, how it grades) rather than
anything about Gemini.
"""

import json
import types

import pytest

from arhiteq_api.config import get_settings
from arhiteq_api.models import RetellLLM
from arhiteq_api.services import simulation


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
        ],
    )
    transcript = await sim.run()
    assert [(t["role"], t["content"]) for t in transcript] == [
        ("agent", "Hi, this is Clara."),
        ("user", "I'd like a callback."),
        ("agent", "Sure, when suits you?"),
    ]
    # The greeting was replayed verbatim, not generated.
    assert len(model.prompts) == 3


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
    assert len(model.prompts) == 4
    # The result is visible to the next turn's prompt.
    assert "CB-42" in model.prompts[-1]


async def test_unmocked_tool_result_is_synthesized(monkeypatch):
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Call me back."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"status": "queued"},  # the synthesized tool payload
            {"action": "speak", "content": "All set."},
            {"action": "hangup", "reason": "done"},
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
        ],
    )
    transcript = await sim.run()
    tool_calls = [t for t in transcript if t["role"] == "tool_call_invocation"]
    assert len(tool_calls) == simulation.MAX_TOOL_CALLS_PER_TURN
    assert "kept calling tools" in transcript[-1]["content"]


async def test_run_stops_at_the_turn_cap(monkeypatch):
    chatter = [
        {"action": "speak", "content": "and another thing"},
        {"action": "speak", "content": "I see"},
    ] * simulation.MAX_TURNS
    sim, _ = make_simulator(monkeypatch, chatter)
    transcript = await sim.run()
    # greeting + MAX_TURNS user/agent pairs, and no further model calls.
    assert len(transcript) == 1 + 2 * simulation.MAX_TURNS


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


async def test_judge_skips_the_model_when_there_are_no_metrics(monkeypatch):
    sim, model = make_simulator(monkeypatch, [])
    assert await sim.judge() == ("pass", [])
    assert model.prompts == []


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


async def test_generate_raises_when_nothing_usable_comes_back(monkeypatch):
    client, _ = fake_client([{"test_cases": []}])
    monkeypatch.setattr(simulation, "build_genai_client", lambda _s: client)
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    with pytest.raises(RuntimeError, match="no usable test cases"):
        await simulation.generate_test_cases(_llm_with_tools(), 2)
