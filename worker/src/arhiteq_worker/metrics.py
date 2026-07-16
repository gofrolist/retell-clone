"""Prometheus metrics for the Arhiteq voice worker.

Exposed on ``:9090`` (override with ``ARHITEQ_METRICS_PORT``). Series names
match docs/ARCHITECTURE.md "Observability".

NOTE: livekit-agents runs each job in a subprocess by default; every process
that records metrics starts (or tries to start) its own exporter and silently
skips if the port is taken. For exact aggregate numbers across job processes
use prometheus_client multiprocess mode (TODO if per-process scrape via the
pod is not enough).
"""

from __future__ import annotations

import logging
import os

from prometheus_client import Counter, Histogram, start_http_server

logger = logging.getLogger("arhiteq-worker.metrics")

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

_server_started = False


def ensure_server(port: int | None = None) -> None:
    """Start the metrics HTTP server once per process; ignore port clashes."""
    global _server_started
    if _server_started:
        return
    if port is None:
        port = int(os.getenv("ARHITEQ_METRICS_PORT", "9090"))
    try:
        start_http_server(port)
        _server_started = True
        logger.info("metrics server listening on :%d", port)
    except OSError as exc:
        # Another worker process on this host already exposes the port.
        _server_started = True
        logger.debug("metrics server not started (port %d): %s", port, exc)
