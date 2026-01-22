# Changelog

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
