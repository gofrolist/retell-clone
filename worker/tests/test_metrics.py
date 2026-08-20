"""The worker's counters are incremented in livekit job subprocesses, so the
thing worth testing is that a *parent* can read them back — that was the bug:
each job process lost the port race, skipped its exporter and took its counts
with it, and nothing was ever scraped.

These tests spawn real subprocesses rather than mock them, because the whole
mechanism (multiprocess mode is chosen at prometheus_client import time, from
an env var the child inherits) only exists across a process boundary.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, multiprocess

from arhiteq_worker import metrics


def _child_holds_jobs(active: int, finished: int) -> None:
    """A job process that ran ``active + finished`` jobs and ended ``finished``.

    Mirrors what ``entrypoint`` does: one ``inc()`` per job, one ``dec()`` in
    the shutdown callback. livekit reuses a job process for several jobs, so
    the interleaving matters more than the count.
    """
    from arhiteq_worker import metrics as child_metrics

    for _ in range(active + finished):
        child_metrics.ACTIVE_JOBS.inc()
    for _ in range(finished):
        child_metrics.ACTIVE_JOBS.dec()


def _child_records(tool: str, times: int) -> None:
    """Stand in for a job subprocess: fresh interpreter state, own pid.

    It does not set ``PROMETHEUS_MULTIPROC_DIR`` itself — by the time this
    runs, importing this module has already imported prometheus_client, which
    fixes the storage class for good. The child has to inherit the variable at
    startup, which is exactly why livekit sets it before spawning job procs.
    """
    from arhiteq_worker import metrics as child_metrics

    for _ in range(times):
        child_metrics.TOOL_CALLS_TOTAL.labels(tool=tool, outcome="success").inc()
    child_metrics.LLM_TTFB_SECONDS.observe(0.25)


def _collect(multiproc_dir: str) -> dict[tuple[str, str], float]:
    """What livekit's /metrics endpoint serves, as {(tool, outcome): value}.

    Keyed by name, not by position: the collector hands labels back sorted
    alphabetically, not in declaration order.
    """
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=multiproc_dir)
    return {
        (sample.labels["tool"], sample.labels["outcome"]): sample.value
        for metric in registry.collect()
        for sample in metric.samples
        if sample.name == "arhiteq_tool_calls_total"
    }


def _run_child(multiproc_dir: str, tool: str, times: int) -> None:
    # "spawn" matches what livekit uses off Linux and gives the child a clean
    # import of prometheus_client, which is what selects multiprocess mode.
    # The variable goes into the parent's environment only so the child
    # inherits it at interpreter startup; the parent already imported
    # prometheus_client and stays in single-process mode.
    previous = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = multiproc_dir
    try:
        proc = mp.get_context("spawn").Process(target=_child_records, args=(tool, times))
        proc.start()
    finally:
        if previous is None:
            del os.environ["PROMETHEUS_MULTIPROC_DIR"]
        else:
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = previous
    proc.join(timeout=60)
    assert proc.exitcode == 0


def test_counters_from_separate_job_processes_sum(tmp_path: Path) -> None:
    multiproc_dir = str(tmp_path)
    _run_child(multiproc_dir, "log_mood", 3)
    _run_child(multiproc_dir, "log_mood", 2)
    _run_child(multiproc_dir, "end_call", 1)

    collected = _collect(multiproc_dir)

    # Both log_mood processes are dead by now; their counts must survive them
    # and add up, or the series resets every call and alerting is worthless.
    assert collected[("log_mood", "success")] == 5.0
    assert collected[("end_call", "success")] == 1.0


def test_parent_collects_without_incrementing_anything(tmp_path: Path) -> None:
    """The supervisor process runs no tool calls; it must still export them."""
    multiproc_dir = str(tmp_path)
    _run_child(multiproc_dir, "web_lookup", 1)

    assert metrics.TOOL_CALLS_TOTAL.labels(tool="web_lookup", outcome="success")._value.get() == 0
    assert _collect(multiproc_dir)[("web_lookup", "success")] == 1.0


def test_histograms_survive_the_process_that_observed_them(tmp_path: Path) -> None:
    multiproc_dir = str(tmp_path)
    _run_child(multiproc_dir, "log_mood", 1)

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=multiproc_dir)
    counts = [
        sample.value
        for metric in registry.collect()
        for sample in metric.samples
        if sample.name == "arhiteq_llm_ttfb_seconds_count"
    ]
    assert counts == [1.0]


def _gauge(multiproc_dir: str, name: str) -> list[float]:
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=multiproc_dir)
    return [
        sample.value
        for metric in registry.collect()
        for sample in metric.samples
        if sample.name == name
    ]


def _run_gauge_child(multiproc_dir: str, active: int, finished: int) -> None:
    previous = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = multiproc_dir
    try:
        proc = mp.get_context("spawn").Process(target=_child_holds_jobs, args=(active, finished))
        proc.start()
    finally:
        if previous is None:
            del os.environ["PROMETHEUS_MULTIPROC_DIR"]
        else:
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = previous
    proc.join(timeout=60)
    assert proc.exitcode == 0


class TestActiveJobs:
    """`arhiteq_worker_active_jobs` — the latency dashboard's concurrency panel
    and the worker HPA's custom metric both name this series, and until now
    nothing ever wrote it."""

    def test_sums_to_one_series_across_job_processes(self, tmp_path: Path) -> None:
        multiproc_dir = str(tmp_path)
        _run_gauge_child(multiproc_dir, active=2, finished=1)
        _run_gauge_child(multiproc_dir, active=1, finished=3)

        # One value, not one per pid: `multiprocess_mode="livesum"` is what
        # keeps the default `all` mode from splitting this by process.
        assert _gauge(multiproc_dir, "arhiteq_worker_active_jobs") == [3.0]

    def test_a_job_that_ends_leaves_nothing_behind(self, tmp_path: Path) -> None:
        multiproc_dir = str(tmp_path)
        _run_gauge_child(multiproc_dir, active=0, finished=4)

        assert _gauge(multiproc_dir, "arhiteq_worker_active_jobs") == [0.0]


class TestExporterOptions:
    def test_defaults_to_the_port_the_probes_and_servicemonitor_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ARHITEQ_METRICS_PORT", raising=False)
        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)

        options = metrics.exporter_options()

        assert options["prometheus_port"] == 9090
        # Must be set, or livekit leaves multiprocess mode off and the job
        # subprocesses go back to being unreadable.
        assert options["prometheus_multiproc_dir"]

    def test_honours_an_explicit_port_and_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARHITEQ_METRICS_PORT", "9123")
        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/var/run/arhiteq-metrics")

        assert metrics.exporter_options() == {
            "prometheus_port": 9123,
            "prometheus_multiproc_dir": "/var/run/arhiteq-metrics",
        }

    def test_port_zero_disables_the_exporter_entirely(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ARHITEQ_METRICS_PORT", "0")

        # No dir either: writing per-process files nobody serves is just litter.
        assert metrics.exporter_options() == {
            "prometheus_port": None,
            "prometheus_multiproc_dir": None,
        }
