"""Per-turn latency, on the session kind production actually runs.

The bug these cover: `RealtimeModelMetrics` has `ttft` and no `ttfb`, and the
handler only recorded a sample inside the `ttfb` branch. Every production call
runs Gemini Live, so every production call finalized with `latency: null` — the
field that answers "is it slower than yesterday?" had never held a number.

The shapes below mirror livekit's own classes, which are **disjoint and emitted
as separate events**: one per component, per turn. Modelling a pipeline turn as
a single object carrying both ttft and ttfb is what let the first version of
this fix double-count — it read "no ttfb" as "realtime", which is also true of
every LLM event.
"""

from __future__ import annotations

from dataclasses import dataclass

from arhiteq_worker import metrics
from arhiteq_worker.state import CallState


@dataclass
class RealtimeMetrics:
    """livekit RealtimeModelMetrics: ttft, input_tokens, no ttfb."""

    ttft: float
    input_tokens: int = 0
    cancelled: bool = False
    type: str = "realtime_model_metrics"


@dataclass
class LLMMetrics:
    """livekit LLMMetrics: ttft and prompt_tokens, no ttfb."""

    ttft: float
    prompt_tokens: int = 0
    cancelled: bool = False
    type: str = "llm_metrics"


@dataclass
class TTSMetrics:
    """livekit TTSMetrics: ttfb and input_tokens (text billing), no ttft."""

    ttfb: float
    input_tokens: int = 0
    cancelled: bool = False
    type: str = "tts_metrics"


@dataclass
class STTMetrics:
    """livekit STTMetrics: input_tokens (audio billing) and nothing else here."""

    input_tokens: int = 0
    type: str = "stt_metrics"


def _feed(*events, carry=None):
    e2e: list[float] = []
    tokens: list[int] = []
    carry = {} if carry is None else carry
    for ev in events:
        metrics.observe_turn(ev, carry=carry, e2e_out=e2e, input_tokens_out=tokens)
    return e2e, tokens


def test_realtime_turn_is_recorded_from_ttft_alone() -> None:
    e2e, _ = _feed(RealtimeMetrics(ttft=0.84))
    assert e2e == [840.0]


def test_one_pipeline_turn_produces_exactly_one_sample() -> None:
    # The regression the first version shipped: STT → LLM → TTS is three
    # events, and the LLM one has no ttfb. Reading that as "realtime" appended
    # 300.0 here as well, so a 420ms turn reported two samples and a p50 of 360.
    e2e, _ = _feed(
        STTMetrics(input_tokens=900),
        LLMMetrics(ttft=0.30, prompt_tokens=27_000),
        TTSMetrics(ttfb=0.12, input_tokens=42),
    )
    assert e2e == [420.0]


def test_pipeline_prompt_size_comes_from_the_llm_event() -> None:
    # STT input_tokens is audio, TTS input_tokens is text; neither is the
    # prompt. Only LLMMetrics.prompt_tokens answers "did the prompt grow".
    _, tokens = _feed(
        STTMetrics(input_tokens=900),
        LLMMetrics(ttft=0.3, prompt_tokens=27_000),
        TTSMetrics(ttfb=0.1, input_tokens=42),
    )
    assert tokens == [27_000]


def test_a_turn_with_no_audio_is_not_a_latency_sample() -> None:
    # ttft = -1 is livekit's "no audio token was sent" — a tool-only turn.
    e2e, _ = _feed(RealtimeMetrics(ttft=-1))
    assert e2e == []


def test_a_barged_in_realtime_turn_is_not_a_latency_sample() -> None:
    # Timing a cancelled generation measures the interruption, not the agent.
    e2e, _ = _feed(RealtimeMetrics(ttft=0.9, cancelled=True))
    assert e2e == []


def test_a_cancelled_llm_turn_does_not_lend_its_place_to_the_next_ttfb() -> None:
    # Without dropping the carry, this TTS leg pairs with the PREVIOUS turn's
    # ttft and records a sample that never happened.
    carry: dict[str, float] = {}
    e2e, _ = _feed(LLMMetrics(ttft=0.30), TTSMetrics(ttfb=0.12), carry=carry)
    assert e2e == [420.0]
    e2e2, _ = _feed(LLMMetrics(ttft=0.5, cancelled=True), TTSMetrics(ttfb=0.2), carry=carry)
    assert e2e2 == []


def test_one_ttft_is_spent_on_one_ttfb() -> None:
    # A stray second TTS segment must not re-use a ttft already paired.
    carry: dict[str, float] = {}
    e2e, _ = _feed(LLMMetrics(ttft=0.30), TTSMetrics(ttfb=0.12), TTSMetrics(ttfb=0.15), carry=carry)
    assert e2e == [420.0]


def test_input_tokens_are_captured_so_size_can_be_correlated() -> None:
    _, tokens = _feed(RealtimeMetrics(ttft=0.5, input_tokens=27_000))
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
