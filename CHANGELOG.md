# Changelog

## [0.2.0] - 2026-01-23

### Added
- Prometheus metrics endpoint (`/metrics`) with counters for events received/queued/rejected/deduped/processed, batch flushes, PA API requests/latency, DLQ events, and queue depth gauge
- Structured JSON logging via `LOG_FORMAT=json` setting (uses `python-json-logger`)
- Dead-letter queue (`userid_dlq` Redis list) for batches that fail after all retries exhausted
- Enhanced `/ready` endpoint that probes Redis ping + HTTPS connectivity to each PA target
- `app/metrics.py` — centralized Prometheus metric definitions
- `app/logging_config.py` — shared logging configuration for API and worker
- `tests/test_metrics.py` — metrics increment verification tests

### Changed
- `send_batch()` now returns `list[str]` of failed target URLs (was `None`)
- Worker loop updates `mist_userid_queue_depth` gauge each iteration
- Worker records batch flush trigger (size/timer/shutdown) in metrics

### Confirmed
- Deduplication was already wired into worker loop at `app/worker.py:89` — no code change needed

### Future (Roadmap)
- Shared/external Redis support for HA (two app instances behind F5)
- `locustfile.py` for load testing (validate 100 events/sec capacity)
- `make status` target showing queue depth, event rates, service health
- PA API key rotation without downtime (reload on SIGHUP)
- Site filtering, rate limiting, username domain filtering

## [0.1.0] - 2025-01-22

### Added
- Webhook receiver for Mist `client-sessions` and `client-join` events
- HMAC-SHA256 signature validation (`X-Mist-Signature-v2`)
- Username resolution: `client_username` (802.1X) with `psk_name` (PSK) fallback
- Redis-backed event queue with deduplication (configurable TTL)
- Async worker process with batched PA XML API calls
- Login/logout classification via `next_ap` field (login wins over logout)
- Multi-target Palo Alto support (individual firewalls or Panorama)
- Exponential backoff retry for transient PA API failures
- Health check endpoints (`/health`, `/ready`)
- Graceful shutdown with batch flush on SIGTERM
- systemd watchdog integration (`sd_notify`)
- systemd unit files with memory limits and security hardening
- Makefile for install/configure/deploy workflow
- Comprehensive test suite with pytest-asyncio
