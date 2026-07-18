"""Tests for the Gemini-Live goodbye/closing-line detector.

The detector arms the Live-only safety-net hangup: on native-audio Gemini Live
the model emits the ``end_call`` tool late (often only on the *next* user turn),
so we proactively hang up a short grace after the agent voices a closing line.
False positives only matter when the user then stays silent, so the pattern is
tuned to real sign-offs, not any polite phrase.
"""

from __future__ import annotations

import pytest

from arhiteq_worker.goodbye import looks_like_goodbye


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
