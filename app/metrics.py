from prometheus_client import Counter, Gauge, Histogram

# Webhook receiver
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

# Worker
EVENTS_DEDUPED = Counter(
    "mist_userid_events_deduped_total", "Events skipped by dedup"
)
EVENTS_INVALID_USERNAME = Counter(
    "mist_userid_events_invalid_username_total", "Events with invalid usernames",
    ["reason"],
)
EVENTS_PROCESSED = Counter(
    "mist_userid_events_processed_total", "Events processed", ["action"]
)
BATCH_FLUSHES = Counter(
    "mist_userid_batch_flushes_total", "Batch flushes", ["trigger"]
)
BATCH_SIZE = Histogram(
    "mist_userid_batch_size", "Events per batch",
    buckets=[1, 5, 10, 25, 50, 100],
)

# PA API
PA_REQUESTS = Counter(
    "mist_userid_pa_requests_total", "PA API requests", ["target", "status"]
)
PA_REQUEST_DURATION = Histogram(
    "mist_userid_pa_request_duration_seconds", "PA API latency", ["target"]
)

# Dead-letter queue
DLQ_EVENTS = Counter(
    "mist_userid_dlq_events_total", "Events sent to DLQ"
)

# Queue depth (gauge, updated each worker loop)
QUEUE_DEPTH = Gauge(
    "mist_userid_queue_depth", "Redis queue depth"
)
