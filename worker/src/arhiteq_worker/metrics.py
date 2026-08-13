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

from prometheus_client import Counter, Histogram

JOBS_TOTAL = Counter(
    "arhiteq_worker_jobs_total",
    "Voice agent jobs handled by this worker",
    ["direction"],
)

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
    """
    ttft = getattr(m, "ttft", None)
    ttfb = getattr(m, "ttfb", None)
    if getattr(m, "cancelled", False):
        return

    if ttft is not None and ttft >= 0:
        LLM_TTFB_SECONDS.observe(ttft)
        carry["value"] = ttft

    input_tokens = getattr(m, "input_tokens", None)
    if isinstance(input_tokens, int) and input_tokens > 0:
        input_tokens_out.append(input_tokens)

    if ttfb is not None and ttfb >= 0:
        TTS_TTFB_SECONDS.observe(ttfb)
        e2e_out.append((carry.get("value", 0.0) + ttfb) * 1000.0)
    elif ttfb is None and ttft is not None and ttft >= 0:
        # Realtime: no synthesis step to add, so first audio token is the wait.
        e2e_out.append(ttft * 1000.0)


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
