"""Closing-line ("goodbye") detection for the Gemini Live safety-net hangup.

On a native-audio Gemini Live session the model voices its goodbye and then
*defers* the ``end_call`` tool call to its next turn — which only fires when
fresh user input arrives. That leaves several seconds of dead air before the
call tears down. main.py arms a short silence timer once the agent voices a
closing line and this module decides what counts as one.

Kept livekit-free so it's unit-testable in the dev-only test group (which does
not install the heavy livekit-agents stack).
"""

from __future__ import annotations

import re

# Unambiguous closing tokens — safe to match anywhere in the line, since they
# essentially never occur non-terminally in agent speech.
_STRONG = r"good\s?bye|bye(?:[-\s]?bye)?|farewell"

# Softer cues that ALSO occur mid-sentence ("I'll take care of that", "we can
# talk later about X", "did you have a good night's sleep"). These count as a
# sign-off only when they sit at the END of the line — otherwise a premature
# hangup would fire on an engaged caller who merely paused. Kept broad enough to
# catch common phrasings ("have a good rest of your day", "take good care").
_TERMINAL = (
    r"take\s+(?:good\s+|great\s+)?care|"
    r"take\s+it\s+easy|"
    r"good\s?night|"
    r"talk\s+to\s+you\s+(?:later|soon|again|tomorrow)|"
    r"talk\s+(?:later|soon)|"
    r"(?:see|catch)\s+you\s+(?:later|soon|around|tomorrow)|"
    r"have\s+a\s+(?:good|great|nice|wonderful|lovely|pleasant)\s+"
    r"(?:rest\s+of\s+your\s+)?(?:day|night|evening|afternoon|morning|one|weekend|week)"
)

# What may follow a terminal cue and still count as the end of the line:
#   - trailing punctuation, with an optional closing particle ("take care now",
#     "talk soon then", "take care for now"); or
#   - a comma-separated short vocative tail ("take care, friend!").
# The comma is required for the vocative form so a continuation clause ("take
# care of that", no comma) does not qualify as a sign-off.
_END = (
    r"(?:"
    r"\s*(?:(?:for\s+)?now|then|already)?\s*[.!?…]*"
    r"|[,;—-]+\s*(?:[\w']+\s*){1,3}[.!?…]*"
    r")\s*$"
)

_GOODBYE_RE = re.compile(
    rf"\b(?:{_STRONG})\b|\b(?:{_TERMINAL})\b{_END}",
    re.IGNORECASE,
)


def looks_like_goodbye(text: str | None) -> bool:
    """True when *text* reads as an agent sign-off / closing line."""
    if not text:
        return False
    return _GOODBYE_RE.search(text) is not None
