# Changelog

## [0.2.0] - Unreleased (Roadmap)

### Observability
- Prometheus metrics endpoint (`/metrics`) — event counts, batch success/failure rates, queue depth, PA response times
- Structured JSON logging option for log aggregation

### Reliability
- Verify dedup is wired into worker loop (module exists but may not be called)
- Dead-letter queue for events that fail after max retries
- `/ready` endpoint that probes Redis ping + PA target connectivity

### HA / F5 Prep
- Shared/external Redis support (two app instances behind F5)
- Stateless verification (no local state that breaks with multiple nodes)
- F5 health monitor compatible responses

### Operational
- `locustfile.py` for load testing (validate 100 events/sec capacity)
- `make status` target showing queue depth, event rates, service health
- PA API key rotation without downtime (reload on SIGHUP)

### Filtering / Safety
- Site filtering — only process events from specific `site_id` values
- Rate limiting on webhook endpoint (Redis-based)
- Username domain filtering (only push `@example.edu`, ignore guest accounts)

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
