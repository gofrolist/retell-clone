from prometheus_client import Counter, Gauge, Histogram

CALLS_TOTAL = Counter(
    "arhiteq_calls_total", "Calls by direction and final status", ["direction", "status"]
)
CALLS_ONGOING = Gauge("arhiteq_calls_ongoing", "Calls currently ongoing")
CALL_DURATION = Histogram(
    "arhiteq_call_duration_seconds",
    "Call talk time in seconds",
    buckets=(5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
WEBHOOK_DELIVERIES = Counter(
    "arhiteq_webhook_deliveries_total",
    "Webhook delivery attempts by event and outcome",
    ["event", "outcome"],
)
INBOUND_RESOLUTIONS = Counter(
    "arhiteq_inbound_resolutions_total",
    "Inbound webhook routing results",
    ["outcome"],  # webhook_ok | webhook_failed_fallback | no_webhook
)
ANALYSIS_RUNS = Counter("arhiteq_analysis_runs_total", "Post-call analysis runs", ["outcome"])
