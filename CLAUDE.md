# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary

High-throughput webhook receiver that collects username-to-IP mappings from Juniper Mist wireless and pushes them to Palo Alto firewalls via the XML User-ID API. Designed for 10,000+ campus users with 100+ events/second peak capacity. Full spec in `SPEC.md`.

## Architecture

```
Mist Webhooks → FastAPI (uvicorn) → Redis Queue → Worker Process → Palo Alto XML API
                                          ↓
                                   Deduplication Cache (Redis, 5-min TTL)
```

Two systemd services: an API receiver (returns 202 immediately) and a separate worker that batches mappings (50 items or 1-2s flush timer) before sending to PA firewalls.

## Tech Stack & Platform

- Python 3.11+ on RHEL 9
- FastAPI + uvicorn (async, multi-worker)
- Redis (queue, dedup cache, rate limiting)
- httpx (async HTTP client with connection pooling)
- pydantic-settings for configuration
- pytest + pytest-asyncio for testing

## Versioning

- Semantic versioning, current: **0.1.0**
- Version tracked in `app/__init__.py`
- Git tags: `v0.1.0`, `v0.2.0`, etc.
- Open source (MIT License)

## Build & Run Commands

```bash
# Install (or use Makefile)
make install                             # venv + deps
pip install -r requirements.txt          # manual

# Run API server (development)
uvicorn app.main:app --reload

# Run worker process
python -m app.worker

# Run tests
pytest
pytest tests/test_webhook.py             # single test file
pytest tests/test_webhook.py::test_name  # single test

# Load testing
locust -f tests/locustfile.py

# Deploy to production
make deploy                              # install systemd units + start

# Health check endpoints
# GET /health  — liveness
# GET /ready   — readiness (Redis + PA targets)
```

## Platform Constraints (RHEL 9)

- Use `dnf`, never `apt`
- Use `podman` if containerization needed, not Docker
- Services use systemd unit files (`mist-userid-api.service`, `mist-userid-worker.service`)
- Logs go to journald or `/var/log/`
- SELinux remains enforcing — use proper contexts

## Key Design Decisions

- **Async everywhere**: webhook handlers never block on PA API calls
- **Batch PA API calls**: `/api/?type=user-id` supports multiple `<login>` and `<logout>` entries per request
- **Webhook topics**: `client-sessions` + `client-join` — uses `client_username` (or `psk_name` fallback) + `client_ip`; `client-join` always login, `client-sessions` uses `next_ap` for login vs. logout
- **Logout on disconnect**: send `<logout>` when `next_ap == "000000000000"` (true disconnect); login wins over logout for same user+IP in a batch
- **Multi-target PA support**: configurable list of PA firewall/Panorama targets (`PA_TARGETS` env var)
- **Retry with exponential backoff**: failed PA API batches retry up to 5 times (1s, 2s, 4s, 8s, 16s)
- **Deduplication**: identical user+IP pairs within 5-min TTL are skipped
- **Mist webhook signature validation**: HMAC-SHA256 via `X-Mist-Signature-v2` header; reject invalid (401)
- **Graceful shutdown + watchdog**: worker flushes batch on SIGTERM; calls `sd_notify(WATCHDOG=1)` each loop to prove liveness
- **Credentials via env vars or Vault**: never in code
- **Sanitize usernames in debug logs**
