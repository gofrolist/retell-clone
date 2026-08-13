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

from arhiteq_worker.live_speech import live_verbatim_instructions


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


def _runtime(session, agent="default"):
    """A CallRuntime with a say handler bound, the way entrypoint binds it."""
    from arhiteq_worker.main import CallRuntime, speak_verbatim

    runtime = CallRuntime.__new__(CallRuntime)
    runtime._agent_swap = None
    bound = FakeAgent() if agent == "default" else agent
    runtime._bound_agent = bound

    async def handler(text: str, *, allow_interruptions: bool = True) -> None:
        await speak_verbatim(
            session,
            bound,
            text=text,
            call_id="call_test",
            allow_interruptions=allow_interruptions,
        )

    runtime._say = handler
    return runtime


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
    pinned = runtime._bound_agent.history[0]
    assert live_verbatim_instructions("Sorry we missed you.") in pinned
    # ...and the instructions are put back, so the line is not a standing order.
    assert runtime._bound_agent.instructions == "BASE"


@pytest.mark.asyncio
async def test_live_say_does_not_lose_the_authored_wording_silently() -> None:
    session = FakeLiveSession()
    runtime = _runtime(session, agent=None)  # nothing to pin onto
    await runtime.say("Sorry we missed you.")
    # Still asks for the turn rather than dropping it, per _pinned_turn.
    assert session.generated == 1
