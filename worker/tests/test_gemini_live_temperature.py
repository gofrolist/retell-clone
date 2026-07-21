"""Gemini Live sampling temperature policy.

Native-audio models sample text and audio tokens with a single temperature;
forwarding the agent's text-LLM model_temperature (Retell default 0) makes the
speech degenerate into droning/repeated syllables. The Live session therefore
keeps the model default unless ARHITEQ_GEMINI_LIVE_TEMPERATURE pins one.
"""

import pytest

from arhiteq_worker.config import gemini_live_temperature


def test_unset_env_means_model_default() -> None:
    assert gemini_live_temperature(None) is None


@pytest.mark.parametrize("raw", ["", "abc", "nan", "0.5.1"])
def test_unparseable_values_fall_back_to_model_default(raw: str) -> None:
    assert gemini_live_temperature(raw) is None


@pytest.mark.parametrize("raw", ["-0.1", "2.5", "100"])
def test_out_of_range_values_fall_back_to_model_default(raw: str) -> None:
    assert gemini_live_temperature(raw) is None


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), ("0.8", 0.8), ("1", 1.0), ("2", 2.0)])
def test_explicit_override_is_parsed(raw: str, expected: float) -> None:
    assert gemini_live_temperature(raw) == expected
