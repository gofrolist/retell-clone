"""Verbatim-line directives for a Gemini Live (speech-to-speech) session.

A Live session has no TTS, so nothing on the worker side can voice a fixed
line — only the realtime model can say it. livekit's obvious lever for that,
``session.generate_reply(instructions=…)``, is unusable on the google plugin:
``realtime_api.RealtimeSession.generate_reply`` replays the instructions as a
**model** turn and then appends a placeholder ``"."`` user turn to trigger a
generation. Gemini therefore reads the directive as something it has already
said, and the ``"."`` as the caller's reply — so it answers a message nobody
spoke instead of speaking the line.

Two production calls showed both failure shapes (2026-08-05, Clara check-in on
``gemini-live-2.5-flash-native-audio``):

- the model read the placeholder as an unanswered greeting and, four seconds
  in, fired ``log_mood`` ("listener did not respond… assuming neutral") →
  ``log_outcome(user_busy)`` → ``end_call``, never voicing a word;
- on the retry it opened with "I'm glad to hear that. You were a 3 this
  morning — how are you now?" — a *second* turn, invented on top of a greeting
  it believed it had already delivered.

The fix is to put the line in the session's **system instructions**, where
Gemini reads it as a directive rather than as its own past speech, and to name
the ``"."`` placeholder so it stops being mistaken for the caller.

Kept livekit-free so it is unit-testable in the dev-only test group.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("arhiteq-worker.live_speech")

# The placeholder the google plugin sends to trigger a generation. Named in the
# directive so the model stops scoring it as a (non-)answer from the caller.
_PLACEHOLDER_NOTE = (
    'A turn containing only "." may appear in the transcript. It is a technical '
    "placeholder, not speech from the other person: never treat it as an answer, "
    "a refusal, or silence, and never log an outcome or end the call over it."
)


# What the destination agent of a swap has to be told, because the rebuilt
# socket no longer shows it (see live_handoff_instructions).
_HANDOFF_NOTE = (
    "You have just been handed a call that is already in progress. The call did "
    "NOT restart: do not greet the other person, do not introduce yourself, do "
    "not start over. Pick the conversation up at their last turn.\n"
    "Two things are missing from what you can see:\n"
    "- The tool calls made before the handoff. The spoken turns are all there, "
    "the calls behind them are not, so you cannot tell which lookups have "
    "already run. Read the transcript first: an answer already in it has "
    "already been looked up and already been said — do not look it up again and "
    "do not say it again.\n"
    "- The handoff itself. If the transcript reads as though the other person's "
    "subject has just been dealt with by someone who could not finish it, that "
    "is what happened, and it was handed to you. Handing it straight back would "
    "hand it to the agent that gave it up. Answer it yourself, say plainly that "
    "it cannot be answered, or move the conversation on."
)


def _verbatim_block(*, header: str, when: str, line: str, lead: str = "") -> str:
    """An instructions suffix pinning *line* as the model's next spoken turn."""
    return (
        f"\n\n## {header}\n"
        f"{lead}"
        f"{when} is exactly the line between the markers below — word for word, "
        "nothing before it, nothing after it, and no tool call before it.\n"
        "<<<SAY EXACTLY>>>\n"
        f"{line}\n"
        "<<<END>>>"
    )


def live_placeholder_note() -> str:
    """Disarm the ``"."`` turn. Belongs on EVERY Live session's instructions.

    Not only the ones that pin a line: the plugin sends the placeholder ahead
    of any ``generate_reply``, so an agent with no begin_message, or a flow node
    asked to phrase its own line, meets it just as unprepared as the call that
    read it as silence and hung up.
    """
    return f'\n\n## THE "." TURN\n{_PLACEHOLDER_NOTE}'


def live_opening_instructions(greeting: str) -> str:
    """Pin *greeting* as the opening line of a Live call.

    Appended to the agent's instructions before the session starts, so the
    greeting rides in the realtime setup message and costs no mid-call push.
    """
    return live_placeholder_note() + _verbatim_block(
        header="OPENING LINE — this overrides everything above for your first turn",
        lead="The call has just connected and the other person has not spoken yet.\n",
        when="Your first spoken turn of this call",
        line=greeting,
    )


def live_handoff_instructions() -> str:
    """Brief the destination agent of a Live ``agent_swap`` on what it can see.

    A swap changes the tool set, which a Live socket carries in its setup
    message, so the connection is rebuilt (see `_settle_realtime_session`). The
    plugin replays the chat context into the new socket with
    ``exclude_function_call=True, exclude_handoff=True`` — the spoken turns
    survive, every tool call and result does not. The destination agent
    therefore arrives at a transcript with no record of the lookups behind it,
    nor of the handoff that just put it there, and reads the moment as the
    start of a call.

    Production call call_6ed66e6ae63f4a95f6f9294e42dd641f showed all three
    consequences in thirty seconds: the same knowledge-base query run twice and
    the same canned answer spoken twice, a subject handed back and immediately
    handed over again (past a prompt rule forbidding exactly that, which could
    not fire on evidence the model no longer had), and — on the last hand-back,
    to a caller who had just said goodbye — the check-in agent's opening
    greeting instead of a goodbye. The caller had to say bye twice.

    Also carries the placeholder note, and must: ``update_instructions``
    replaces the whole string, so the suffix `ArhiteqAgent.__init__` put on the
    opening instructions is gone from the first swap onwards — including the
    note that stops the plugin's ``"."`` from reading as caller silence.
    """
    return live_placeholder_note() + f"\n\n## MID-CALL HANDOFF\n{_HANDOFF_NOTE}"


def live_verbatim_instructions(line: str) -> str:
    """Pin *line* as the next spoken turn of a Live call (mid-call lines).

    Pushed as a temporary instructions update and removed once the line has
    been voiced — unlike the opening block, this one must not outlive its turn.
    Carries no placeholder note: it is appended to instructions that already
    hold one (`_FlowWiring.set_instructions` puts it on every node).
    """
    return _verbatim_block(
        header="SAY THIS NOW — this overrides everything above for your next turn",
        when="Your next spoken turn",
        line=line,
    )


async def speak_verbatim(
    session: Any,
    agent: Any,
    *,
    text: str,
    call_id: str = "",
    allow_interruptions: bool | None = None,
) -> None:
    """Say *text* word for word, on either kind of session.

    A Gemini Live session is built without a TTS, so ``session.say`` raises
    there — nothing worker-side can voice a line and only the model can. The
    line therefore goes into the session's instructions for exactly one turn
    (see live_speech for why instructions and not ``generate_reply``), and the
    instructions are put back afterwards so it does not read as a standing
    order.

    Every caller that speaks a fixed line has to come through here. The AMD
    path did not: it called ``session.say`` directly, so on every production
    call the voicemail message raised, was swallowed by a ``except Exception``
    and logged at WARNING, and the call hung up in silence looking exactly like
    an ordinary machine_detected.

    ``allow_interruptions`` only reaches the pipeline path; on Live the turn is
    the model's own generation and there is nothing to hand the flag to. None —
    the default — means "don't override", and the flag is left off the
    ``session.say`` call so livekit falls back to the session's own setting
    (which the worker derives from the agent's interruption_sensitivity).
    Passing it explicitly regardless would make a static line interruptible on
    an agent configured with interruption_sensitivity 0.
    """
    if session is None:
        return
    live = getattr(session, "tts", None) is None
    if not live:
        if allow_interruptions is None:
            await session.say(text)
        else:
            await session.say(text, allow_interruptions=allow_interruptions)
        return

    base = getattr(agent, "instructions", None)
    if agent is None or not isinstance(base, str):
        # Nothing to pin onto. Still ask for the turn rather than dropping its
        # slot, but say so: the authored wording is lost and the model will
        # improvise something in its place.
        logger.warning(
            "call=%s: a static line could not be pinned, the model will improvise it",
            call_id,
        )
        await session.generate_reply()
        return
    await agent.update_instructions(f"{base}{live_verbatim_instructions(text)}")
    try:
        await session.generate_reply()
    finally:
        await agent.update_instructions(base)
