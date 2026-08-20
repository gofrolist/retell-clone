"""Prometheus metrics for the Arhiteq voice worker.

Exposed on ``:9090/metrics`` (override with ``ARHITEQ_METRICS_PORT``). Series
names match docs/ARCHITECTURE.md "Observability".

livekit-agents runs every call in its own job subprocess, and every counter
below is incremented *there* — the supervisor process increments none of them.
So the exporter has to read across processes: ``_run()`` in main.py hands
livekit the ``prometheus_port`` / ``prometheus_multiproc_dir`` options from
`exporter_options`, and livekit serves ``/metrics`` from a
``MultiProcessCollector`` registry over that directory. prometheus_client
switches into multiprocess mode from ``PROMETHEUS_MULTIPROC_DIR``, which
livekit sets before it spawns any job process, so the metric objects here are
file-backed in the children and the aggregate is exact.

An in-process ``start_http_server`` cannot do this job: the supervisor binds
the port first, so every job subprocess loses the race, skips its own exporter
and takes its counts to the grave — which is why none of these series were
ever scraped before.

Two consequences of multiprocess mode worth knowing:

- A series only appears once some job process has written it. There are no
  zero-valued series at startup, so alerts must tolerate an absent series.
- Each job process leaves a ``*_<pid>.db`` behind — prometheus_client keeps
  them deliberately, so counters stay monotonic across process exits, and
  livekit never calls ``mark_process_dead``. livekit wipes the directory when
  the worker starts, so they are bounded by one pod's lifetime; the directory
  must therefore be pod-local and ephemeral, never a shared volume.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

JOBS_TOTAL = Counter(
    "arhiteq_worker_jobs_total",
    "Voice agent jobs handled by this worker",
    ["direction"],
)

ACTIVE_JOBS = Gauge(
    "arhiteq_worker_active_jobs",
    "Voice agent jobs this worker is running right now",
    multiprocess_mode="livesum",
)
"""Concurrency, for the latency dashboard and the worker HPA's custom metric.

``livesum`` rather than the default ``all``: the default appends a ``pid``
label, which would make one series per job process ever run on the pod — the
opposite of a gauge you can sum.

This is incremented and decremented in the *job* process, not the supervisor,
for the reason in this module's docstring: the supervisor's own metric objects
are in-process and the ``/metrics`` endpoint only serves the multiprocess
directory, so anything the supervisor sets is invisible. That means a job
process killed outright (OOM, SIGKILL) never runs its decrement and leaks +1
until the pod restarts — prometheus_client keeps the file, and livekit never
calls ``mark_process_dead``. Normal endings, including errors, decrement in
``entrypoint``'s shutdown callback.
"""

TOOL_CALLS_TOTAL = Counter(
    "arhiteq_tool_calls_total",
    "Custom/built-in tool invocations by the voice agent",
    ["tool", "outcome"],
)

_TTFB_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)

LLM_TTFB_SECONDS = Histogram(
    "arhiteq_llm_ttfb_seconds",
    "Time to first LLM token",
    buckets=_TTFB_BUCKETS,
)

TTS_TTFB_SECONDS = Histogram(
    "arhiteq_tts_ttfb_seconds",
    "Time to first synthesized audio byte",
    buckets=_TTFB_BUCKETS,
)

AMD_DETECTIONS_TOTAL = Counter(
    "arhiteq_amd_detections_total",
    "Answering-machine detection verdicts",
    ["result"],
)


def observe_turn(
    m: Any,
    *,
    carry: dict[str, float],
    e2e_out: list[float],
    input_tokens_out: list[int],
) -> None:
    """Record one generation's latency, whichever kind of session produced it.

    A pipeline turn reports two numbers — the LLM's time to first token and the
    TTS's time to first byte — and the caller waits for both, so end-to-end is
    their sum.

    A **realtime** turn reports neither: ``RealtimeModelMetrics`` carries
    ``ttft`` ("time to first audio token") and has no ``ttfb`` at all, because
    a speech-to-speech model has no separate synthesis step. That is the whole
    bug this function exists to fix. The previous code only appended a sample
    inside the ``ttfb`` branch, so on a realtime session it appended nothing,
    ever — and since production runs Gemini Live on every call, every call
    finalized with ``latency: null``. The one question the field is there to
    answer ("is it slower than it was?") had no data behind it at all.

    For a realtime turn ``ttft`` IS the end-to-end wait: first audio token out
    is the moment the listener hears something.

    ``ttft`` is ``-1`` when a generation produced no audio (a tool-only turn),
    and cancelled generations are barged-in turns whose timing measures the
    interruption rather than the agent. Neither is a latency sample.

    **Dispatch is on the metric type, never on which attributes are missing.**
    livekit emits one event per component, and the classes are disjoint:
    ``LLMMetrics`` has ``ttft`` and no ``ttfb``, ``TTSMetrics`` has ``ttfb`` and
    no ``ttft``, ``STTMetrics`` has neither. "No ttfb, so this must be realtime"
    is therefore true of every LLM event on a pipeline session, and reading it
    that way appended two samples per turn — the LLM leg alone, then the real
    sum — halving the reported p50 on exactly the sessions it was not meant to
    touch.
    """
    kind = getattr(m, "type", "")

    if kind == "realtime_model_metrics":
        # Speech-to-speech: one event per generation, no synthesis leg to add,
        # so first audio token out is the whole wait the listener feels.
        if getattr(m, "cancelled", False):
            return
        ttft = getattr(m, "ttft", -1)
        tokens = getattr(m, "input_tokens", 0)
        if isinstance(tokens, int) and tokens > 0:
            input_tokens_out.append(tokens)
        if ttft is not None and ttft >= 0:
            LLM_TTFB_SECONDS.observe(ttft)
            e2e_out.append(ttft * 1000.0)
        return

    if kind == "llm_metrics":
        # Half of a pipeline turn: hold ttft until its TTS leg arrives.
        if getattr(m, "cancelled", False):
            # Drop the carry too. A cancelled generation whose TTS segment
            # still reports would otherwise be paired with the PREVIOUS turn's
            # ttft and recorded as one plausible-looking sample.
            carry.pop("value", None)
            return
        # LLMMetrics calls the prompt `prompt_tokens`; `input_tokens` on the
        # STT/TTS events is audio and text billing, not prompt size.
        prompt_tokens = getattr(m, "prompt_tokens", 0)
        if isinstance(prompt_tokens, int) and prompt_tokens > 0:
            input_tokens_out.append(prompt_tokens)
        ttft = getattr(m, "ttft", -1)
        if ttft is not None and ttft >= 0:
            LLM_TTFB_SECONDS.observe(ttft)
            carry["value"] = ttft
        return

    if kind == "tts_metrics":
        if getattr(m, "cancelled", False):
            carry.pop("value", None)
            return
        ttfb = getattr(m, "ttfb", -1)
        if ttfb is None or ttfb < 0:
            return
        TTS_TTFB_SECONDS.observe(ttfb)
        # Pair with the ttft this turn set, and consume it: one ttft belongs to
        # one ttfb, and a leftover would pair with the next turn.
        ttft = carry.pop("value", None)
        if ttft is not None:
            e2e_out.append((ttft + ttfb) * 1000.0)
        return

    # stt / vad / eou / interruption / avatar: not a turn latency.


_DEFAULT_MULTIPROC_DIR = os.path.join(tempfile.gettempdir(), "arhiteq-worker-metrics")


def exporter_options() -> dict[str, Any]:
    """livekit ``AgentServer`` kwargs that make the series above scrapable."""
    port = int(os.getenv("ARHITEQ_METRICS_PORT", "9090"))
    if not port:
        # Escape hatch for running a second worker on one host. Deployments
        # leave this alone: the k8s probes tcpSocket the same port.
        return {"prometheus_port": None, "prometheus_multiproc_dir": None}
    return {
        "prometheus_port": port,
        "prometheus_multiproc_dir": (
            os.getenv("PROMETHEUS_MULTIPROC_DIR") or _DEFAULT_MULTIPROC_DIR
        ),
    }
