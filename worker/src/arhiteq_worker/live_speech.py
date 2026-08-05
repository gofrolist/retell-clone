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
        "<<<END>>>\n"
        f"{_PLACEHOLDER_NOTE}"
    )


def live_opening_instructions(greeting: str) -> str:
    """Pin *greeting* as the opening line of a Live call.

    Appended to the agent's instructions before the session starts, so the
    greeting rides in the realtime setup message and costs no mid-call push.
    """
    return _verbatim_block(
        header="OPENING LINE — this overrides everything above for your first turn",
        lead="The call has just connected and the other person has not spoken yet.\n",
        when="Your first spoken turn of this call",
        line=greeting,
    )


def live_verbatim_instructions(line: str) -> str:
    """Pin *line* as the next spoken turn of a Live call (mid-call lines).

    Pushed as a temporary instructions update and removed once the line has
    been voiced — unlike the opening block, this one must not outlive its turn.
    """
    return _verbatim_block(
        header="SAY THIS NOW — this overrides everything above for your next turn",
        when="Your next spoken turn",
        line=line,
    )
