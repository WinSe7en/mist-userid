"""API (webhook receiver) metrics, served by the API's /metrics.

Worker metrics live in app.worker_metrics. Keep them apart: in Prometheus
multiprocess mode every metric the API process creates is exported, so worker
metrics imported here would show up on the API as permanent zeros.
"""

from prometheus_client import Counter

EVENTS_RECEIVED = Counter(
    "mist_userid_events_received_total", "Events received", ["topic"]
)
EVENTS_QUEUED = Counter(
    "mist_userid_events_queued_total", "Events queued to Redis"
)
EVENTS_REJECTED = Counter(
    "mist_userid_events_rejected_total", "Events rejected", ["reason"]
)
WEBHOOK_QUEUE_FULL = Counter(
    "mist_userid_webhook_queue_full_total", "Webhooks rejected due to full queue"
)
