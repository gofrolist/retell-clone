"""Per-turn latency, on the session kind production actually runs.

The bug these cover: `RealtimeModelMetrics` has `ttft` and no `ttfb`, and the
handler only recorded a sample inside the `ttfb` branch. Every production call
runs Gemini Live, so every production call finalized with `latency: null` — the
field that answers "is it slower than yesterday?" had never held a number.
"""

from __future__ import annotations

from dataclasses import dataclass

from arhiteq_worker import metrics
from arhiteq_worker.state import CallState


@dataclass
class RealtimeMetrics:
    """Shape of livekit's RealtimeModelMetrics: no `ttfb` attribute at all."""

    ttft: float
    input_tokens: int = 0
    cancelled: bool = False


@dataclass
class PipelineMetrics:
    """Shape of the STT→LLM→TTS path, where the caller waits for both legs."""

    ttft: float
    ttfb: float
    input_tokens: int = 0
    cancelled: bool = False


def _observe(m, carry=None):
    e2e: list[float] = []
    tokens: list[int] = []
    metrics.observe_turn(
        m, carry=carry if carry is not None else {}, e2e_out=e2e, input_tokens_out=tokens
    )
    return e2e, tokens


def test_realtime_turn_is_recorded_from_ttft_alone() -> None:
    e2e, _ = _observe(RealtimeMetrics(ttft=0.84))
    assert e2e == [840.0]


def test_pipeline_turn_still_sums_both_legs() -> None:
    carry: dict[str, float] = {}
    e2e, _ = _observe(PipelineMetrics(ttft=0.30, ttfb=0.12), carry=carry)
    assert e2e == [420.0]
    assert carry["value"] == 0.30


def test_a_turn_with_no_audio_is_not_a_latency_sample() -> None:
    # ttft = -1 is livekit's "no audio token was sent" — a tool-only turn.
    e2e, _ = _observe(RealtimeMetrics(ttft=-1))
    assert e2e == []


def test_a_barged_in_turn_is_not_a_latency_sample() -> None:
    # Timing a cancelled generation measures the interruption, not the agent.
    e2e, _ = _observe(RealtimeMetrics(ttft=0.9, cancelled=True))
    assert e2e == []


def test_input_tokens_are_captured_so_size_can_be_correlated() -> None:
    _, tokens = _observe(RealtimeMetrics(ttft=0.5, input_tokens=27_000))
    assert tokens == [27_000]


def test_finalize_payload_carries_realtime_latency() -> None:
    state = CallState()
    state.call_id = "call_x"
    state.answered_at_ms = 1
    for sample in (
        RealtimeMetrics(ttft=0.5, input_tokens=27_000),
        RealtimeMetrics(ttft=1.5, input_tokens=27_100),
    ):
        metrics.observe_turn(
            sample,
            carry={},
            e2e_out=state.e2e_latency_ms,
            input_tokens_out=state.input_tokens,
        )
    latency = state.build_finalize_payload()["latency"]
    assert latency["e2e"]["num"] == 2
    assert latency["e2e"]["max"] == 1500.0
    assert latency["input_tokens"]["max"] == 27_100


def test_no_samples_leaves_latency_null_rather_than_zero() -> None:
    state = CallState()
    state.call_id = "call_x"
    state.answered_at_ms = 1
    assert state.build_finalize_payload()["latency"] is None
