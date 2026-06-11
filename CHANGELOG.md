# Changelog

## [Unreleased]

### Added
- **Daily PA cert expiry check**: `scripts/check_pa_certs.sh` + `mist-userid-certcheck.timer` (08:00 daily) — warns via journald/StellarCyber when a PA cert is within 30 days of expiry; would have prevented the May 2026 incident. Also exposes `mist.userid.cert.days[<host>]` Zabbix UserParameter
- **`PA_VERIFY_SSL` break-glass option** (default `true`): temporarily disable PA TLS verification during a cert incident so mappings keep flowing while the cert is renewed; worker logs a WARNING at startup while disabled (see runbook)

### Changed
- **Python 3.11+ now required** (`requires-python = ">=3.11"`); pyproject version synced (was stale at 0.3.0), ruff target → py311
- Modernized typing syntax (`X | None`, builtin generics) and worker queue read now uses `asyncio.timeout()` (3.11+)
- **Fixed latent bug**: keygen non-200 responses raised `httpx.HTTPStatusError(request=None)`, which newer httpx rejects with `TypeError` — now raises `ValueError` with the target and status in the message

### Ops
- **2026-06-10 — Python upgraded 3.9.21 → 3.12.13** (RHEL 9 app stream): production venv rebuilt at `/opt/mist-userid/venv`, all 104 tests pass, ~50s API downtime during cutover. Old venv kept at `/opt/mist-userid/venv-py39-old` for rollback; remove after a week of clean operation. Note: venvs are not relocatable (console-script shebangs bake in absolute paths) — always build at the final path.

## [0.3.2] - 2026-05-28

### Fixed
- **Watchdog false-positive crash** (root cause of May 9 and May 28 worker crashes): the systemd watchdog was fed once per worker loop iteration, but a batch flush during a PA outage legitimately blocks the loop for 31s+ of retry backoff — exceeding `WatchdogSec=30` and getting the worker SIGABRT'd mid-flush. The watchdog is now fed every 10s by a dedicated asyncio task (`watchdog_heartbeat()`); a frozen event loop still trips it, so real hang detection is preserved
- **Worker crash on malformed events**: per-event processing is now wrapped in an exception guard — a single bad event (e.g. non-string `client_username` from Mist) can no longer kill the worker and dump its in-memory batch
- **Worker crash-loop during Redis restarts**: `RedisError` on `brpop` now backs off 5s and retries instead of crashing every `RestartSec`
- **Integer IPs passed webhook validation**: `ipaddress.ip_address()` accepts integers, so `is_valid_ip()` now requires a string; an integer IP previously queued successfully and then crashed the worker during XML serialization
- **Type guards in webhook**: non-dict event entries, non-string usernames, and non-string SSIDs are rejected with metrics instead of raising (`malformed_event` / `invalid_username` rejection reasons added)
- `validate_username()` returns `(False, "not_a_string")` for non-string input instead of raising `TypeError`

### Ops
- **2026-05-28 — PA SSL cert expiry incident**: pan03 cert expired 2026-05-21, causing daily DLQ failures and eventual worker watchdog crash (SIGABRT). Both pan03 and pan04 certs renewed (valid through 2026-12-12). Root cause of months of intermittent DLQ entries identified.
- **Set calendar reminder for ~2026-11-12** to renew both PA certs before Dec 12 expiry

## [0.3.1] - 2026-03-11

### Changed
- `USERID_TIMEOUT` increased from 60 min to 360 min (6 hrs) to prevent wireless-only users from falling out of the PA User-ID table during stationary sessions; logout events still remove mappings immediately on disconnect
- Stale event log line now includes age in seconds, site name, AP name, and SSID for faster triage during retry storms
- `env.example` USERID_TIMEOUT guidance updated for mixed DHCP lease environments (students 16hr vs faculty/staff 2hr)

### Ops
- NTP enabled: `chronyd` configured with campus time servers (`ntp.example.edu` — 192.0.2.10 / 192.0.2.11)
- SELinux policy updated: `syslogd_port_t` now allows TCP 5555 for StellarCyber log forwarding via rsyslogd
- `docs/runbook.md` added covering 7 operational procedures: DLQ entries, stale event spikes, PA auth failures, Mist webhook reset, building onboarding, service won't start, high queue depth; includes config reference table and known issues history

## [0.3.0] - 2026-02-17

### Added
- **Webhook replay protection**: Events older than `WEBHOOK_MAX_AGE` (default 300s) are rejected, preventing replay attacks with stale user-to-IP mappings
- **Queue depth cap**: Webhooks return 429 when Redis queue reaches `MAX_QUEUE_DEPTH` (default 10,000), preventing unbounded queue growth under DoS
- **IP validation hardening**: Loopback (`127.0.0.0/8`) and multicast (`224.0.0.0/4`) IPs are now rejected in addition to link-local and unspecified
- **Events type validation**: Non-list `events` field returns 400 instead of crashing
- **DLQ size cap**: Dead-letter queue capped at 1,000 entries via `LTRIM` (oldest dropped)
- **Queue-full metric**: `mist_userid_webhook_queue_full_total` counter tracks 429 rejections
- `WEBHOOK_MAX_AGE` and `MAX_QUEUE_DEPTH` config settings
- 6 new security-focused tests (stale events, queue full, loopback/multicast IP, non-list events)

### Changed
- `is_valid_ip()` uses `addr.is_unspecified` instead of explicit `0.0.0.0` check (covers IPv6 `::` too)
- nginx config restricts `/ready` and `/metrics` to `10.0.0.0/8` and `127.0.0.1`
- systemd units hardened with `CapabilityBoundingSet=`, `RestrictAddressFamilies`, `PrivateDevices`, `RestrictNamespaces`, `LockPersonality`
- Test fixtures use dynamic timestamps (compatible with replay protection)

### Removed
- Redundant null byte check in `validate_username()` (already covered by control character check)

### Fixed (post-release)
- `EVENTS_RECEIVED` metric was incremented before queue depth check, causing over-count when 429 is returned; moved to after the guard
- Queue-full test hardcoded `10000` instead of reading from settings; now derived from `get_settings().max_queue_depth`
- `MAX_QUEUE_DEPTH` and `WEBHOOK_MAX_AGE` added to conftest test environment (previously relied on silent defaults)
- `MAX_QUEUE_DEPTH` and `WEBHOOK_MAX_AGE` added to README and SPEC configuration reference tables

### Docs (post-release)
- Added **Scaling & Performance** section to README with capacity thresholds, metrics to watch during building rollout, and documented optimization opportunities (Redis pipeline, `time.time()` capture, `ignore_ssid_set` caching)
- Expanded **Future: High Availability** section with full migration guide for F5 + two app servers + dedicated Redis server, including rolling update procedure and config change table

## [0.2.1] - 2026-01-26

### Added
- **PA API key auto-generation**: New `PA_USERNAME` and `PA_PASSWORD` config options allow the service to generate API keys automatically at startup via the PA keygen API, eliminating the need to store static API keys
- `app/pa_auth.py` — API key generation, caching, and auto-refresh logic
- `tests/test_pa_auth.py` — comprehensive tests for keygen and caching
- API key status in `/ready` endpoint response
- Comprehensive `deploy/env.example` with detailed documentation for all options
- DHCP lease alignment guidance for `USERID_TIMEOUT` setting
- GitHub Actions CI workflow (tests on Python 3.9-3.12)
- `CONTRIBUTING.md` — development guidelines for contributors
- `SECURITY.md` — security policy and vulnerability reporting

### Changed
- `PA_API_KEY` is now optional if `PA_USERNAME` and `PA_PASSWORD` are provided
- 401 responses now trigger automatic API key refresh and retry (once)
- Worker pre-validates/generates API key at startup (fail-fast behavior)
- `pyproject.toml` for modern Python packaging (replaces setup.py)

### Fixed
- Python version requirement corrected to 3.9+ (was incorrectly stated as 3.11+ in some docs)

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

### Must Do (Pending)
- ~~**Fix NTP**~~ — Done 2026-03-06: `chronyd` enabled, synced to `ntp.example.edu` (192.0.2.10 / 192.0.2.11), offset -20µs
- **Escalate pan03 to network team** — 3 isolated failures logged (Feb 28, Mar 5, earlier); shared management path suspected
- **Import Zabbix template** — `deploy/zabbix/mist-userid-template.yaml` written but never imported; no automated DLQ alerting until done
- **Add Zabbix trigger for stale event rate spike** — `EVENTS_REJECTED{reason="stale_event"}` metric exists but no alert threshold; spike to 29,000 events went undetected until manual review
- ~~**Verify `userid_timeout` (60 min)**~~ — Done 2026-03-06: increased to 360 min (6 hrs); balances student DHCP lease (16 hrs) vs faculty/staff lease (2 hrs); faculty/staff stale window acceptable given AD User-ID as primary source; logout events still clean up immediately on disconnect
- **Sanitize repo data (public GitHub repo!)** — sweep for real internal data and replace with example.edu equivalents: real student usernames + org/site/wlan UUIDs + AP/building names in `tests/conftest.py` (captured from live events), PA hostnames (`pan0x-*.mgmt.example.edu`) in runbook/zabbix conf/CHANGELOG, campus IPs in README examples and runbook (time servers, client IPs), building names throughout docs. Decide whether to also scrub git history (rewrite + force push) or accept history as-is and only fix HEAD
- **Update documentation and requirements** — refresh requirements.txt pins for Python 3.12 baseline (consider `pip-audit` for CVE check), align README/SPEC with current architecture (cert check timer, PA_VERIFY_SSL, watchdog heartbeat)
- **DLQ replay mechanism** — failed batches are currently lost; need a script or worker feature to retry DLQ entries after PA recovers
- **Periodic mapping refresh** — users stationary on one AP for >8 hrs (overnight residential, all-day office) will fall out of PA table; extend building onboarding script to run on a schedule (every ~7 hrs) querying Mist API for connected clients and re-pushing their mappings
- ~~**Write runbook/SOP**~~ — Done 2026-03-11: `docs/runbook.md` covering DLQ, stale event spikes, PA auth failures, webhook reset, building onboarding, service won't start, high queue depth, key config reference, and known issues history

### Future (Roadmap)
- **Bump `WEBHOOK_MAX_AGE` to 600s** — natural Mist batching delay observed at 316–761s; current 300s default rejects some legitimate events
- **Redis auth + TLS** — required before HA migration when Redis port is exposed on the network
- Shared/external Redis support for HA (two app instances behind F5)
- `locustfile.py` for load testing (validate 100 events/sec capacity)
- `make status` target showing queue depth, event rates, service health
- Site filtering, rate limiting, username domain filtering
- **Building onboarding script**: query Mist REST API (`GET /api/v1/sites/{site_id}/stats/clients`) for currently-connected clients at a site and bulk-register all user-to-IP mappings with PA via a one-shot `<login>` batch; eliminates the gap where users connected before webhook activation are unknown to PA until they roam or reconnect

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
