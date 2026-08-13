"""The voicemail message has to be spoken on the session kind production runs.

A Gemini Live session has no TTS, so `session.say()` raises there. The AMD path
called it directly, caught the exception and logged a warning — so on every
production call the message an agent was configured to leave was silently not
left, and the call hung up looking exactly like an ordinary machine_detected.

`CallRuntime.say` already knows both paths (it is what the flow uses for a
static line): `session.say` on a pipeline session, and on Live the line pinned
into the model's instructions via live_speech. These tests hold the AMD path to
using it.
"""

from __future__ import annotations

import pytest

from arhiteq_worker.live_speech import live_verbatim_instructions, speak_verbatim


class FakePipelineSession:
    """A session with TTS: say() works and records what it was told.

    `tts` being non-None is exactly how the worker tells the two apart — a Live
    session is built without one.
    """

    tts = object()

    def __init__(self) -> None:
        self.said: list[tuple[str, bool]] = []
        self.generated = 0

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        self.said.append((text, allow_interruptions))

    async def generate_reply(self) -> None:
        self.generated += 1


class FakeLiveSession:
    """Gemini Live: no TTS, so say() raises exactly as livekit's does."""

    tts = None

    def __init__(self) -> None:
        self.generated = 0

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        raise RuntimeError(
            "trying to generate speech from text without a TTS model or a "
            "RealtimeSession that supports say(); add a TTS model to AgentSession"
        )

    async def generate_reply(self) -> None:
        self.generated += 1


class FakeAgent:
    def __init__(self, instructions: str = "BASE") -> None:
        self.instructions = instructions
        self.history: list[str] = []

    async def update_instructions(self, text: str) -> None:
        self.instructions = text
        self.history.append(text)


class _Speaker:
    """Stands in for what `CallRuntime.say` delegates to.

    Deliberately does not import `arhiteq_worker.main`: that pulls the whole
    livekit stack in, and the worker's CI test group installs the dev group
    without it — the same reason live_speech is kept import-free. The logic is
    therefore tested where it lives.
    """

    def __init__(self, session, agent="default") -> None:
        self.session = session
        self.agent = FakeAgent() if agent == "default" else agent

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        await speak_verbatim(
            self.session,
            self.agent,
            text=text,
            call_id="call_test",
            allow_interruptions=allow_interruptions,
        )


def _runtime(session, agent="default"):
    return _Speaker(session, agent)


@pytest.mark.asyncio
async def test_pipeline_voicemail_is_spoken_uninterruptibly() -> None:
    session = FakePipelineSession()
    await _runtime(session).say("Sorry we missed you.", allow_interruptions=False)
    assert session.said == [("Sorry we missed you.", False)]


@pytest.mark.asyncio
async def test_live_voicemail_is_pinned_instead_of_raising() -> None:
    # The regression: this used to reach session.say and blow up, so nothing
    # was ever left on an answering machine.
    session = FakeLiveSession()
    runtime = _runtime(session)
    await runtime.say("Sorry we missed you.", allow_interruptions=False)
    assert session.generated == 1, "the model was never asked to speak the line"
    pinned = runtime.agent.history[0]
    assert live_verbatim_instructions("Sorry we missed you.") in pinned
    # ...and the instructions are put back, so the line is not a standing order.
    assert runtime.agent.instructions == "BASE"


@pytest.mark.asyncio
async def test_live_say_does_not_lose_the_authored_wording_silently() -> None:
    session = FakeLiveSession()
    runtime = _runtime(session, agent=None)  # nothing to pin onto
    await runtime.say("Sorry we missed you.")
    # Still asks for the turn rather than dropping it, per _pinned_turn.
    assert session.generated == 1
