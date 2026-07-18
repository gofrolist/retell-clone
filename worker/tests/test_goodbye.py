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
        "Have a great day!",
        "Have a wonderful evening.",
        "Talk to you later.",
        "I'll talk to you soon.",
        "See you tomorrow.",
        "Farewell for now.",
        "Alright, take it easy.",
        "GOODBYE.",  # case-insensitive
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
    ],
)
def test_ignores_non_closing_lines(text: str) -> None:
    assert looks_like_goodbye(text) is False


def test_none_is_safe() -> None:
    assert looks_like_goodbye(None) is False
