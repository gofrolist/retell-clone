"""The voicemail message has to be spoken on the session kind production runs.

A Gemini Live session has no TTS, so `session.say()` raises there. The AMD path
called it directly, caught the exception and logged a warning — so on every
production call the message an agent was configured to leave was silently not
left, and the call hung up looking exactly like an ordinary machine_detected.

`CallRuntime.say` already knows both paths (it is what the flow uses for a
static line): `session.say` on a pipeline session, and on Live the line pinned
into the model's instructions via live_speech.

Two layers here. The `speak_verbatim` tests are livekit-free and run in the
dev-only group; the `_run_amd` ones need `arhiteq_worker.main` (which imports
livekit at module scope) and are what actually hold the AMD path to speaking
through the runtime instead of the session.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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

    async def say(self, text: str, *, allow_interruptions: bool | None = None) -> None:
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


@pytest.mark.asyncio
async def test_pipeline_say_leaves_the_interruption_default_alone() -> None:
    """No flag given → none forwarded, so the session's own setting decides.

    A flow's static lines come through here with nothing passed. Forwarding a
    hardcoded True would make them interruptible on an agent configured with
    interruption_sensitivity 0, whose session default is False.
    """

    class _StrictSession(FakePipelineSession):
        async def say(self, text: str, **kwargs) -> None:
            self.said.append((text, kwargs))

    session = _StrictSession()
    await _runtime(session).say("A static line.")
    assert session.said == [("A static line.", {})]


# --- the AMD path itself -----------------------------------------------------
# Needs arhiteq_worker.main, which imports livekit at module scope, so these
# skip in the dev-only test group (see tests/test_dial_answer.py for the same
# gate). They are the ones that fail if the AMD path goes back to session.say.


def _main():
    pytest.importorskip("livekit.agents")
    from arhiteq_worker import main

    return main


class _RecordingRuntime:
    """CallRuntime's speech/hangup surface, as _run_amd is allowed to use it."""

    def __init__(self, say=None) -> None:
        self.said: list[tuple[str, bool | None]] = []
        self.ended: list[tuple[str, bool]] = []
        self._say = say

    async def say(self, text: str, *, allow_interruptions: bool | None = None) -> None:
        self.said.append((text, allow_interruptions))
        if self._say is not None:
            await self._say()

    async def end_call(self, reason: str, *, flush_grace: bool = False) -> None:
        self.ended.append((reason, flush_grace))


def _machine_participant():
    """A SIP participant carrying Telnyx's machine verdict — skips the LLM."""
    return SimpleNamespace(attributes={"sip.h.x-telnyx-amd-result": "machine"})


def _amd_cfg(main, message: str):
    return main.CallConfig.from_dict(
        {
            "call_id": "call_amd",
            "agent": {"voicemail_option": {"action": {"type": "static_text", "text": message}}},
        }
    )


async def _run_amd(main, runtime, *, message: str, variables=None):
    cfg = _amd_cfg(main, message)
    await main._run_amd(
        cfg,
        main.CallState(call_id="call_amd"),
        runtime,
        llm=None,
        participant=_machine_participant(),
        amd_speech=[],
        amd_window_open={"open": True},
        variables=variables if variables is not None else {},
    )


@pytest.mark.asyncio
async def test_amd_speaks_the_message_through_the_runtime() -> None:
    """The regression: this used to be `session.say`, which raises on Live."""
    main = _main()
    runtime = _RecordingRuntime()
    await _run_amd(main, runtime, message="Sorry we missed you.")
    assert runtime.said == [("Sorry we missed you.", False)]
    assert runtime.ended == [("machine_detected", True)]


@pytest.mark.asyncio
async def test_amd_resolves_the_message_template() -> None:
    main = _main()
    runtime = _RecordingRuntime()
    await _run_amd(
        main,
        runtime,
        message="Hi {{first_name}}, sorry we missed you.",
        variables={"first_name": "Ada"},
    )
    assert runtime.said == [("Hi Ada, sorry we missed you.", False)]


@pytest.mark.asyncio
async def test_amd_hangs_up_even_if_the_message_never_finishes(monkeypatch) -> None:
    """On Live the say awaits a model generation that may never land.

    Unbounded, the machine_detected hangup queues behind it and the only
    backstop left is the hour-long max-duration watchdog.
    """
    main = _main()
    monkeypatch.setattr(main, "VOICEMAIL_SAY_TIMEOUT_S", 0.05)
    runtime = _RecordingRuntime(say=lambda: asyncio.sleep(3600))
    await asyncio.wait_for(_run_amd(main, runtime, message="Sorry we missed you."), timeout=5)
    assert runtime.ended == [("machine_detected", True)]


@pytest.mark.asyncio
async def test_amd_hangs_up_when_speaking_raises() -> None:
    main = _main()

    async def _boom():
        raise RuntimeError("no TTS")

    runtime = _RecordingRuntime(say=_boom)
    await _run_amd(main, runtime, message="Sorry we missed you.")
    assert runtime.ended == [("machine_detected", True)]


@pytest.mark.asyncio
async def test_call_runtime_say_reaches_the_bound_speaker() -> None:
    """`set_say` is the seam entrypoint wires speak_verbatim onto."""
    main = _main()
    control = main.CallRuntime(None, None, main.CallState(call_id="call_amd"))
    session = FakeLiveSession()
    agent = FakeAgent()

    async def _speak(text: str, *, allow_interruptions: bool | None = None) -> None:
        await speak_verbatim(
            session, agent, text=text, call_id="call_amd", allow_interruptions=allow_interruptions
        )

    control.set_say(_speak)
    await control.say("Sorry we missed you.", allow_interruptions=False)
    assert session.generated == 1
    assert live_verbatim_instructions("Sorry we missed you.") in agent.history[0]


@pytest.mark.asyncio
async def test_call_runtime_say_without_a_session_is_survivable() -> None:
    main = _main()
    control = main.CallRuntime(None, None, main.CallState(call_id="call_amd"))
    await control.say("Sorry we missed you.")  # warns, does not raise
