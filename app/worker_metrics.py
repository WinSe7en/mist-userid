"""Worker metrics, served by the worker's own endpoint (WORKER_METRICS_PORT).

Registered on a dedicated registry so the worker endpoint carries only worker
metrics. Must never be imported by the API process (see app.metrics).
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.process_collector import ProcessCollector

WORKER_REGISTRY = CollectorRegistry()
ProcessCollector(registry=WORKER_REGISTRY)

EVENTS_DEDUPED = Counter(
    "mist_userid_events_deduped_total", "Events skipped by dedup",
    registry=WORKER_REGISTRY,
)
EVENTS_INVALID_USERNAME = Counter(
    "mist_userid_events_invalid_username_total", "Events with invalid usernames",
    ["reason"], registry=WORKER_REGISTRY,
)
EVENTS_PROCESSED = Counter(
    "mist_userid_events_processed_total", "Events processed", ["action"],
    registry=WORKER_REGISTRY,
)
BATCH_FLUSHES = Counter(
    "mist_userid_batch_flushes_total", "Batch flushes", ["trigger"],
    registry=WORKER_REGISTRY,
)
BATCH_SIZE = Histogram(
    "mist_userid_batch_size", "Events per batch",
    buckets=[1, 5, 10, 25, 50, 100], registry=WORKER_REGISTRY,
)
PA_REQUESTS = Counter(
    "mist_userid_pa_requests_total", "PA API requests", ["target", "status"],
    registry=WORKER_REGISTRY,
)
PA_REQUEST_DURATION = Histogram(
    "mist_userid_pa_request_duration_seconds", "PA API latency", ["target"],
    registry=WORKER_REGISTRY,
)
DLQ_EVENTS = Counter(
    "mist_userid_dlq_events_total", "Batches sent to DLQ",
    registry=WORKER_REGISTRY,
)
QUEUE_DEPTH = Gauge(
    "mist_userid_queue_depth", "Redis queue depth (updated each worker loop)",
    registry=WORKER_REGISTRY,
)
