"""How `ArhiteqAgent` opens a call. Requires the livekit stack.

The regression under test: on a Gemini Live (speech-to-speech) session the
greeting used to be delivered as ``generate_reply(instructions=…)``. livekit's
google plugin replays those instructions as a **model** turn followed by a
placeholder ``"."`` user turn, so Gemini believed it had already greeted the
caller and answered a message nobody spoke — or, reading the placeholder as
silence, logged the call as ``user_busy`` and hung up four seconds in. Both
shapes were seen in production on 2026-08-05.
"""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("livekit.agents")

from arhiteq_worker.live_speech import live_placeholder_note
from arhiteq_worker.main import ArhiteqAgent

GREETING = "Hi Tom, it's Clara — how are you doing today?"
PROMPT = "You are Clara."


class _FakeSession:
    """Enough AgentSession for `on_enter`: a TTS marker and the two seams."""

    def __init__(self, *, tts: bool) -> None:
        self.tts = object() if tts else None
        self.said: list[str] = []
        self.replies: list[str | None] = []

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        self.said.append(text)

    def generate_reply(self, instructions: str | None = None):
        self.replies.append(instructions)


def _enter(agent: ArhiteqAgent, session: _FakeSession) -> None:
    # `Agent.session` reads through the running activity; a stub activity is
    # all `on_enter` needs from the framework.
    agent._activity = SimpleNamespace(session=session)  # type: ignore[assignment]
    asyncio.run(agent.on_enter())


def _agent(**kwargs) -> ArhiteqAgent:
    base = {
        "instructions": PROMPT,
        "tools": [],
        "begin_message": GREETING,
        "start_speaker": "agent",
    }
    return ArhiteqAgent(**{**base, **kwargs})  # type: ignore[arg-type]


def test_a_live_agent_pins_the_greeting_in_its_instructions() -> None:
    agent = _agent(live=True)
    assert PROMPT in agent.instructions
    assert f"<<<SAY EXACTLY>>>\n{GREETING}\n<<<END>>>" in agent.instructions


def test_a_live_agent_asks_for_the_opening_turn_without_instructions() -> None:
    # instructions= is the poisoned lever: it arrives as a model turn.
    session = _FakeSession(tts=False)
    _enter(_agent(live=True), session)
    assert session.said == []
    assert session.replies == [None]


def test_a_pipeline_agent_still_speaks_the_greeting_through_tts() -> None:
    session = _FakeSession(tts=True)
    _enter(_agent(live=False), session)
    assert session.said == [GREETING]
    assert session.replies == []


def test_a_live_agent_that_does_not_open_the_call_pins_no_greeting() -> None:
    # start_speaker="user": the callee talks first, so a pinned "your first
    # turn is the greeting" would have the model talk over them. The
    # placeholder note still belongs there — the "." arrives regardless.
    agent = _agent(live=True, start_speaker="user")
    assert "<<<SAY EXACTLY>>>" not in agent.instructions
    assert live_placeholder_note() in agent.instructions
    session = _FakeSession(tts=False)
    _enter(agent, session)
    assert session.said == []
    assert session.replies == []


def test_a_live_agent_with_no_begin_message_still_disarms_the_placeholder() -> None:
    # The exact wire conditions of the call that read the "." as silence and
    # hung up four seconds in — minus a greeting to pin.
    agent = _agent(live=True, begin_message=None)
    assert live_placeholder_note() in agent.instructions
    session = _FakeSession(tts=False)
    _enter(agent, session)
    assert session.replies == [None]


def test_a_pipeline_agent_is_told_nothing_about_the_placeholder() -> None:
    # There is no placeholder on the pipeline path — it is a realtime-plugin
    # artifact. Explaining a turn that never arrives is just prompt noise.
    assert _agent(live=False).instructions == PROMPT


def test_an_agent_without_a_begin_message_just_asks_for_a_turn() -> None:
    session = _FakeSession(tts=True)
    _enter(_agent(begin_message=None), session)
    assert session.said == []
    assert session.replies == [None]
