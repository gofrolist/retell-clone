from prometheus_client import Counter, Gauge, Histogram

CALLS_TOTAL = Counter(
    "architeq_calls_total", "Calls by direction and final status", ["direction", "status"]
)
CALLS_ONGOING = Gauge("architeq_calls_ongoing", "Calls currently ongoing")
CALL_DURATION = Histogram(
    "architeq_call_duration_seconds",
    "Call talk time in seconds",
    buckets=(5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
WEBHOOK_DELIVERIES = Counter(
    "architeq_webhook_deliveries_total",
    "Webhook delivery attempts by event and outcome",
    ["event", "outcome"],
)
INBOUND_RESOLUTIONS = Counter(
    "architeq_inbound_resolutions_total",
    "Inbound webhook routing results",
    ["outcome"],  # webhook_ok | webhook_failed_fallback | no_webhook
)
ANALYSIS_RUNS = Counter("architeq_analysis_runs_total", "Post-call analysis runs", ["outcome"])
