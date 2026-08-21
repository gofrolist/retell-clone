"""Tests for the Gemini-Live goodbye/closing-line detector.

The detector arms the Live-only safety-net hangup: on native-audio Gemini Live
the model emits the ``end_call`` tool late (often only on the *next* user turn),
so we proactively hang up a short grace after the agent voices a closing line.
False positives only matter when the user then stays silent, so the pattern is
tuned to real sign-offs, not any polite phrase.
"""

from __future__ import annotations

import asyncio

import pytest

from arhiteq_worker.goodbye import hang_up_after_goodbye, looks_like_goodbye


@pytest.mark.parametrize(
    "text",
    [
        "Take care, friend!",
        "Take care, friend. I'll talk to you tomorrow.",
        "Okay, goodbye!",
        "Bye now.",
        "Bye-bye!",
        "Good night, sleep well.",
        "Good night!",
        "Have a great day!",
        "Have a wonderful evening.",
        "Talk to you later.",
        "I'll talk to you soon.",
        "See you tomorrow.",
        "Farewell for now.",
        "Alright, take it easy.",
        "GOODBYE.",  # case-insensitive
        # Recall gap (was missed by the rigid patterns):
        "Have a good rest of your day!",
        "You take good care now.",
        "Okay, take care, friend — talk soon!",
    ],
)
def test_detects_closing_lines(text: str) -> None:
    assert looks_like_goodbye(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "How are you doing today?",
        "That's wonderful to hear. Did you sleep alright?",
        "Sounds good! Are you making coffee, or going out for some?",
        "Is there anything else I can help you with today?",
        "Maybe we can chat about the weather.",  # 'maybe' must not trip 'bye'
        # Mid-sentence uses of soft cues must NOT arm a hangup (they end a clause,
        # not the call) — this is the core "never fires mid-conversation" property:
        "Sure, I'll take care of that for you.",
        "We can talk later about your appointment.",
        "Did you have a good night's sleep?",
        "Let me see you through this.",
        "I'll have a good look at it and get back to you.",
    ],
)
def test_ignores_non_closing_lines(text: str) -> None:
    assert looks_like_goodbye(text) is False


def test_none_is_safe() -> None:
    assert looks_like_goodbye(None) is False


class _Agent:
    """A session's agent_state and hangup, as the net reaches them."""

    def __init__(self) -> None:
        self.busy = False
        self.hangups = 0

    async def hang_up(self) -> None:
        self.hangups += 1

    async def resume_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self.busy = True


def _wait(agent: _Agent, *, grace: float = 0.02, settle: float = 0.02):
    return hang_up_after_goodbye(
        grace_s=grace,
        settle_s=settle,
        agent_busy=lambda: agent.busy,
        hang_up=agent.hang_up,
    )


def test_hangs_up_on_dead_air() -> None:
    agent = _Agent()
    assert asyncio.run(_wait(agent)) is True
    assert agent.hangups == 1


def test_stands_down_when_the_agent_resumed_during_the_grace() -> None:
    agent = _Agent()

    async def run() -> bool:
        resumer = asyncio.ensure_future(agent.resume_after(0.01))
        hung_up = await _wait(agent)
        await resumer
        return hung_up

    assert asyncio.run(run()) is False
    assert agent.hangups == 0


def test_stands_down_when_the_turn_starts_inside_the_settle_window() -> None:
    """The production shape: goodbye voiced, model still working.

    ``log_outcome`` and the line the model speaks on its result land in the
    wait, so the turn's first audio can arrive after the grace has already
    expired. The net proposed the hangup before that turn existed; it must not
    carry it out over the top of it.
    """
    agent = _Agent()

    async def run() -> bool:
        resumer = asyncio.ensure_future(agent.resume_after(0.03))
        hung_up = await _wait(agent)
        await resumer
        return hung_up

    assert asyncio.run(run()) is False
    assert agent.hangups == 0


def test_stays_cancellable_until_it_has_hung_up() -> None:
    """The regression: the last stretch of the wait used to be unstoppable.

    main.py cancels the armed task when the agent starts speaking again, and
    that cancel reaches nothing once the wait has committed to the teardown. So
    the whole coroutine — not just its first sleep — has to stay interruptible.
    """
    agent = _Agent()

    async def run() -> None:
        task = asyncio.ensure_future(_wait(agent, grace=0.01, settle=0.5))
        await asyncio.sleep(0.05)  # past the grace, deep inside the settle window
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert agent.hangups == 0
