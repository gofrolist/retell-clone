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

# Real sign-offs, not merely polite phrases. A false positive only causes a
# premature hangup when the user *also* goes silent for the whole grace window,
# so the cost is low — but we still keep this to genuine closing cues to avoid
# cutting off an engaged caller.
_GOODBYE_RE = re.compile(
    r"\b(?:"
    r"good\s?bye|"
    r"bye(?:[-\s]?bye)?|"
    r"farewell|"
    r"good\s?night|"
    r"take\s+care|"
    r"take\s+it\s+easy|"
    r"talk\s+to\s+you\s+(?:later|soon|again|tomorrow)|"
    r"talk\s+(?:later|soon)|"
    r"(?:see|catch)\s+you\s+(?:later|soon|around|tomorrow)|"
    r"have\s+a\s+(?:good|great|nice|wonderful|lovely|pleasant)\s+"
    r"(?:day|night|evening|afternoon|morning|one|weekend)"
    r")\b",
    re.IGNORECASE,
)


def looks_like_goodbye(text: str | None) -> bool:
    """True when *text* reads as an agent sign-off / closing line."""
    if not text:
        return False
    return _GOODBYE_RE.search(text) is not None
