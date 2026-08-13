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
    allow_interruptions: bool = True,
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
    the model's own generation and there is nothing to hand the flag to.
    """
    if session is None:
        return
    live = getattr(session, "tts", None) is None
    if not live:
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
