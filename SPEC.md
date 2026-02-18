# CLAUDE.md - Mist to Palo Alto User-ID Integration

## Project Overview
High-throughput webhook receiver that collects username-to-IP mappings from Juniper Mist wireless and pushes them to Palo Alto firewalls via XML API for User-ID.

## Scale Requirements
- **10,000+ users** on campus
- Handle burst traffic (class changes, morning rush)
- Target: 100+ webhook events/second during peaks
- Resilient to Mist retry storms

## Architecture
```
Mist Webhooks → FastAPI (uvicorn workers) → Redis Queue → Worker Process → Palo Alto API
                                                ↓
                                         Deduplication Cache
```

## RHEL Development Standards
- Use `dnf` for package management, never `apt`
- Use `podman` if containerization is needed, not Docker
- Target RHEL 9 / Python 3.11+
- Services should use systemd unit files
- Logs go to journald or /var/log/
- SELinux should remain enforcing—use proper contexts

## Tech Stack
- **FastAPI** with uvicorn (async, multiple workers)
- **Redis** for:
  - Inbound event queue (handle bursts)
  - Deduplication cache (same user/IP seen recently? skip)
  - Rate limiting if needed
- **asyncio + httpx** for async PA API calls
- **systemd** for service management

## Performance Optimizations
- **Batch PA API calls**: Queue mappings, flush every 1-2 seconds or 50 mappings (whichever first)
- **Deduplicate**: Cache user+IP pairs in Redis with 5-min TTL—don't re-push identical mappings
- **Async everywhere**: Don't block webhook response waiting for PA
- **Return 202 fast**: Accept webhook, queue it, respond immediately
- **Connection pooling**: Reuse HTTPS sessions to PA firewalls
- **Multiple uvicorn workers**: Match to CPU cores

## Palo Alto Integration Notes
- Use XML API for User-ID updates: `/api/?type=user-id`
- **Batch format** supports multiple `<login>` and `<logout>` entries per request
- API key auth: static `PA_API_KEY` or auto-generated from `PA_USERNAME`/`PA_PASSWORD` via keygen API
- Key is generated once at startup, cached in memory, auto-refreshes on 401 or session timeout
- Default User-ID timeout: 60 min (configurable via `USERID_TIMEOUT`)
- **Configurable list of PA targets** (`PA_TARGETS` env var, comma-separated):
  - Dev/test: 1-2 individual firewalls
  - Prod: Panorama (redistributes mappings to managed firewalls)

### Batch XML Format
```xml
<uid-message>
  <type>update</type>
  <payload>
    <login>
      <entry name="user1@example.edu" ip="10.7.71.140" timeout="60"/>
      <entry name="user2@example.edu" ip="10.7.71.141" timeout="60"/>
    </login>
    <logout>
      <entry name="user3@example.edu" ip="10.7.71.142"/>
    </logout>
  </payload>
</uid-message>
```

## Webhook Receiver Design
```python
# Pseudocode flow
@app.post("/mist/webhook")
async def receive_webhook(request):
    body = await request.body()
    signature = request.headers.get("X-Mist-Signature-v2")
    if not hmac_sha256_verify(MIST_WEBHOOK_SECRET, body, signature):
        return 401

    payload = json.loads(body)
    topic = payload.get("topic")
    if topic not in ("client-sessions", "client-join"):
        return 202  # Ignore other topics

    for event in payload.get("events", []):
        username = event.get("client_username") or event.get("psk_name")
        ip = event.get("client_ip")
        if username and ip:
            event["_topic"] = topic  # carry topic for worker logic
            await redis.lpush("userid_queue", event)

    return {"status": "accepted"}, 202  # Return FAST
```

## Worker Process Design
```python
# Separate process/service
async def worker():
    batch_logins = {}   # keyed by (username, ip) to dedup within batch
    batch_logouts = {}  # keyed by (username, ip)
    while True:
        event = await redis.brpop("userid_queue", timeout=1)
        if event:
            username = event.get("client_username") or event.get("psk_name")
            ip = event["client_ip"]
            topic = event.get("_topic")
            next_ap = event.get("next_ap", "000000000000")
            key = (username, ip)

            if not is_duplicate(key):  # Redis dedup cache
                if topic == "client-join" or next_ap != "000000000000":
                    # client-join = initial connect; next_ap = roam — login
                    batch_logins[key] = (username, ip)
                    batch_logouts.pop(key, None)  # login supersedes logout
                else:  # true disconnect (next_ap == 000000000000)
                    if key not in batch_logins:  # don't override a login
                        batch_logouts[key] = (username, ip)

        total = len(batch_logins) + len(batch_logouts)
        if total >= BATCH_SIZE or flush_timer_expired():
            await send_batch_to_paloalto(
                list(batch_logins.values()),
                list(batch_logouts.values())
            )
            batch_logins = {}
            batch_logouts = {}
```

### Login vs. Logout Logic
- **`next_ap` is real MAC** (roamed): send `<login>` — user moved APs but is still connected; refresh mapping
- **`next_ap == "000000000000"`** (disconnected): send `<logout>` — user left the network
- **Login always wins**: if same user+IP has both login and logout events in a batch, only login is sent
- Logout entries do not include a `timeout` attribute (immediate removal from PA)
- Deduplication key: `(username, ip)` — prevents redundant API calls during rapid roaming

## Security Requirements
- Validate Mist webhook via `X-Mist-Signature-v2` (HMAC-SHA256) with timing-safe comparison
- HTTPS only (terminate TLS at nginx/haproxy or app)
- No credentials in code—env vars or Vault
- Log auth events but sanitize usernames in debug logs

## Error Handling & Retries
- Retry failed PA API batches with **exponential backoff**
- Max retry attempts: 5 (configurable via `MAX_RETRY_ATTEMPTS`)
- Backoff intervals: 1s, 2s, 4s, 8s, 16s
- After max retries: dead-letter the batch to Redis (`userid_dlq`) for inspection
- **Transient failures** (timeouts, 5xx, commit-window 403): retry with backoff
- **Auth failures** (401, XML `status="unauth"`): invalidate cached API key, regenerate via keygen API, retry once with new key
- **Commit-window 403** ("not authorized for user role"): PAN-OS temporarily revokes API permissions during commits; treated as transient with retry
- **Permanent failures** (genuine 403 permission errors): log immediately, do not retry
- **Benign errors** (HTTP 200 with "Delete mapping failed"): logout for already-expired mapping; treated as success

## Configuration Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `PA_TARGETS` | Comma-separated list of PA firewall/Panorama URLs | *(required)* |
| `PA_API_KEY` | API key for PA XML API (required if username/password not set) | *(empty)* |
| `PA_USERNAME` | PA admin username for API key auto-generation | *(empty)* |
| `PA_PASSWORD` | PA admin password for API key auto-generation | *(empty)* |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379` |
| `MIST_WEBHOOK_SECRET` | Shared secret for webhook signature validation | *(required)* |
| `BATCH_SIZE` | Max items per PA API batch | `50` |
| `BATCH_FLUSH_INTERVAL` | Seconds between batch flushes | `2` |
| `DEDUP_TTL` | Dedup cache TTL in seconds | `300` |
| `MAX_RETRY_ATTEMPTS` | PA API retry limit | `5` |
| `USERID_TIMEOUT` | PA User-ID timeout in minutes | `60` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FORMAT` | Log format: `text` or `json` | `text` |
| `IGNORE_SSIDS` | Comma-separated SSIDs to ignore (case-insensitive) | *(empty)* |
| `MAX_QUEUE_DEPTH` | Reject webhooks with 429 when queue reaches this depth | `10000` |
| `WEBHOOK_MAX_AGE` | Reject events with timestamps older than this many seconds | `300` |

## Graceful Shutdown & Watchdog
- Worker catches `SIGTERM` and flushes the current batch before exiting
- Prevents loss of queued mappings during service restart (`systemctl restart`)
- Worker calls `sd_notify("WATCHDOG=1")` on each batch flush (or idle loop iteration) to satisfy the systemd watchdog
- Use `sdnotify` Python package or `systemd.daemon` for the notify call

## Health Check Endpoints
- `GET /health` — basic liveness check (app is running, returns 200)
- `GET /ready` — readiness check (Redis connected, PA targets reachable)

## Mist Webhook Payload

**Topics**: `client-sessions` and `client-join`
- `client-sessions`: fires when a client session ends (roam, disassociate, or timeout)
- `client-join`: fires on initial connection (provides immediate login mapping)

### Sample Payload
```json
{
  "topic": "client-sessions",
  "events": [
    {
      "ap": "020000000a06",
      "ap_name": "South-173",
      "band": "5",
      "bssid": "020000000a09",
      "client_family": "iPhone",
      "client_hostname": "iPhone",
      "client_ip": "10.7.71.140",
      "client_manufacture": "Apple",
      "client_model": "",
      "client_os": "18.7",
      "client_username": "jsmith@example.edu",
      "connect": 1769117271,
      "connect_float": 1769117271,
      "disconnect": 1769117307,
      "disconnect_float": 1769117307.146,
      "duration": 36.146048459,
      "mac": "020000000c02",
      "next_ap": "020000000a07",
      "org_id": "00000000-0000-4000-8000-000000000001",
      "random_mac": true,
      "rssi": -88,
      "site_id": "00000000-0000-4000-8000-000000000006",
      "site_name": "South_Hall",
      "ssid": "eduroam",
      "termination_reason": 3,
      "timestamp": 1769117307,
      "version": 2,
      "wlan_id": "00000000-0000-4000-8000-000000000003"
    }
  ]
}
```

### Key Fields for User-ID Mapping
| Field | Description |
|-------|-------------|
| `client_username` | 802.1X identity (e.g. `user@example.edu`) — primary username source |
| `psk_name` | PSK credential name (e.g. `User@example.edu`) — fallback username for PSK SSIDs |
| `client_ip` | Client IP address — **required** for mapping (skip event if absent) |
| `termination_reason` | Determines login vs. logout action (see below) |
| `mac` | Client MAC (may be randomized — see `random_mac`) |
| `timestamp` | Event time (epoch seconds) |

### Username Resolution
1. Use `client_username` if present (802.1X / eduroam)
2. Fall back to `psk_name` if present (PSK / Resident_Wireless)
3. Skip the event if neither is available

### Event Classification
Use `next_ap` as the primary signal (more reliable than `termination_reason` alone):

| Condition | Meaning | PA Action |
|-----------|---------|-----------|
| `next_ap` is a real MAC | Roamed — user still connected | `<login>` (refresh mapping) |
| `next_ap == "000000000000"` and `termination_reason == 1` | Disassociated — user left | `<logout>` |
| `next_ap == "000000000000"` and `termination_reason == 2` | Inactive — user timed out | `<logout>` |

### Duplicate Event Handling
Mist sends **paired events** for the same session (same MAC, near-identical timestamps):
- One with `termination_reason: 1` + `next_ap: "000000000000"`
- One with `termination_reason: 3` + `next_ap: <real MAC>`

When multiple events in the same webhook delivery share the same username+IP, **login (roam) takes priority over logout**. A user who roamed is still connected — the logout event for the old AP session should be suppressed.

### Validation Rules
- Skip events where `client_username`/`psk_name` and `client_ip` are both missing/empty
- Skip events where `client_ip` is link-local (169.254.x.x) or unassigned (0.0.0.0)

### Sample: PSK Client (Resident_Wireless) with IP
```json
{
  "ap": "020000000a03",
  "client_ip": "10.6.139.58",
  "mac": "020000000c01",
  "next_ap": "020000000a01",
  "psk_name": "psk-user@example.edu",
  "ssid": "Resident_Wireless",
  "termination_reason": 3,
  "timestamp": 1769117301
}
```
Note: PSK clients may or may not have `client_ip`. Events without `client_ip` are skipped.

### Traffic Profile
Most `client-sessions` events will be **filtered out** at the receiver:
- Many eduroam sessions are short probe/association attempts that never complete 802.1X (no `client_username` or `client_ip`)
- PSK clients frequently lack `client_ip`
- Expect only ~10-20% of inbound events to be actionable User-ID mappings
- Paired events (reason=1 + reason=3) further reduce unique actions via batch dedup

### Undocumented Fields
The fields `client_username`, `client_ip`, and `psk_name` are **not in Mist's official API docs** for `client-sessions` or `client-join`. They appear when 802.1X or PSK authentication is configured — confirmed present in this deployment for both topics. The app handles their absence gracefully (skips the event).

### Initial Connections via `client-join`
`client-sessions` fires at session *end* (roam or disconnect). `client-join` fires on initial connect and **confirmed** to include `client_username`/`client_ip`/`psk_name` in this deployment. All `client-join` events with valid username+IP are treated as `<login>` (no `next_ap` logic needed).

Note: Mist may deliver duplicate `client-join` events for the same connection. The Redis dedup cache (keyed on username+IP) handles this naturally.

### Mist Webhook Configuration
Subscribe to both topics in the Mist site webhook:
```json
{
  "topics": ["client-sessions", "client-join"],
  "type": "http-post",
  "url": "https://<receiver>/mist/webhook",
  "secret": "<MIST_WEBHOOK_SECRET>",
  "enabled": true
}
```

## Webhook Signature Validation
- Header: `X-Mist-Signature-v2` contains HMAC-SHA256(secret, raw_body)
- Fallback header: `X-Mist-Signature` contains HMAC-SHA1(secret, raw_body)
- Use timing-safe comparison to prevent timing attacks
- Reject requests with missing/invalid signatures (return 401)


## Deployment
- Run 2-4 uvicorn workers depending on CPU (`--workers`)
- Separate worker process for PA API calls
- Redis can be local or existing campus Redis
- Consider nginx in front for TLS termination
- Both processes are stateless — all state lives in Redis; restarts lose nothing

## Systemd Hardening (24/7 Production)

### API Service (`mist-userid-api.service`)
```ini
[Unit]
Description=Mist User-ID API Receiver
After=network.target redis.service
Requires=redis.service

[Service]
Type=exec
ExecStart=/opt/mist-userid/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=5
WatchdogSec=30

# Memory protection
MemoryMax=512M
MemoryHigh=400M

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/log/mist-userid

# Environment
EnvironmentFile=/etc/mist-userid/env

[Install]
WantedBy=multi-user.target
```

### Worker Service (`mist-userid-worker.service`)
```ini
[Unit]
Description=Mist User-ID Worker (PA API Sender)
After=network.target redis.service
Requires=redis.service

[Service]
Type=exec
ExecStart=/opt/mist-userid/venv/bin/python -m app.worker
Restart=always
RestartSec=5
WatchdogSec=30

# Memory protection
MemoryMax=256M
MemoryHigh=200M

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/log/mist-userid

# Environment
EnvironmentFile=/etc/mist-userid/env

[Install]
WantedBy=multi-user.target
```

### Key Directives Explained
| Directive | Purpose |
|-----------|---------|
| `Restart=always` | Auto-restart on crash, OOM kill, or watchdog timeout |
| `RestartSec=5` | Wait 5s between restarts (prevents restart storms) |
| `WatchdogSec=30` | Process must call `sd_notify(WATCHDOG=1)` every 30s or get killed (catches hangs) |
| `MemoryMax=512M` | Hard memory ceiling; kernel OOM-kills the process if exceeded |
| `MemoryHigh=400M` | Soft limit; kernel throttles allocations above this (early warning) |
| `NoNewPrivileges=true` | Prevents privilege escalation |
| `ProtectSystem=strict` | Mounts filesystem read-only except explicit `ReadWritePaths` |

### Runtime Architecture (No Threads)
```
                    ┌─────────────────────────────────┐
                    │  mist-userid-api.service         │
                    │  uvicorn --workers 4             │
                    │  ┌─────────┐  ┌─────────┐       │
  Mist Webhooks ──▶│  │ Process 1│  │ Process 2│  ... │──▶ Redis Queue
                    │  │ (asyncio)│  │ (asyncio)│       │
                    │  └─────────┘  └─────────┘       │
                    └─────────────────────────────────┘

                    ┌─────────────────────────────────┐
                    │  mist-userid-worker.service      │
                    │  Single async process            │
  Redis Queue ────▶│  ┌─────────────────────────┐     │──▶ PA Firewalls
                    │  │ asyncio event loop       │     │
                    │  │ (batch + flush timer)    │     │
                    │  └─────────────────────────┘     │
                    └─────────────────────────────────┘
```

- **No threads** — asyncio handles all I/O concurrency in a single thread per process
- **Multiple uvicorn workers** = OS-level processes (fork), not threads; fully independent
- **Worker is single-process** — bounded batch size prevents memory growth
- **Crash recovery** — Redis holds queued events; restarted worker picks up where it left off

## Monitoring
- Prometheus metrics: queue depth, events/sec, PA API latency
- Alert if queue depth exceeds threshold
- Log PA API failures for retry analysis

## Testing
- `pytest` + `pytest-asyncio`
- Load test with `locust` or `hey` before production
- Mock Mist payloads captured from real events

## Versioning
- Semantic versioning, current: **0.3.0**
- Track in `app/__init__.py` (`__version__ = "0.3.0"`)
- Tag releases in git (`git tag v0.1.0`)
- CHANGELOG.md to track notable changes per release

## Open Source & Deployment
- MIT License (permissive, suitable for network tooling)
- Detailed `README.md` covering:
  - Project overview and architecture diagram
  - Prerequisites (Python 3.11+, Redis, RHEL 9)
  - Quick start (install, configure, run)
  - Mist webhook setup instructions (topics, secret, URL)
  - PA firewall API key generation
  - Configuration reference (all env vars with descriptions)
  - systemd service installation steps
  - Health check verification
  - Troubleshooting / FAQ
- Easy deploy script or Makefile:
  - `make install` — create venv, install deps
  - `make configure` — interactive env file setup
  - `make deploy` — copy systemd units, enable/start services
  - `make test` — run test suite
  - `make status` — check service health

## File Structure (Updated)
```
├── app/
│   ├── __init__.py          # __version__ = "0.2.1"
│   ├── main.py              # FastAPI app entry + health endpoints
│   ├── webhook.py           # Webhook routes (POST /mist/webhook)
│   ├── paloalto.py          # PA XML API client (async, batching, retry, DLQ)
│   ├── pa_auth.py           # API key generation (keygen) and caching
│   ├── worker.py            # Queue consumer / batch sender
│   ├── redis_client.py      # Redis connection
│   ├── dedup.py             # Deduplication logic
│   ├── metrics.py           # Prometheus metrics definitions
│   ├── utils.py             # Username sanitization, helpers
│   └── config.py            # Settings via pydantic-settings
├── tests/
│   ├── conftest.py          # Shared fixtures (fake_redis, etc.)
│   ├── test_webhook.py      # Webhook endpoint tests
│   ├── test_worker.py       # Worker logic tests (classify, validate)
│   ├── test_paloalto.py     # PA API client tests (retry, auth, DLQ)
│   ├── test_pa_auth.py      # API key generation/caching tests
│   ├── test_dedup.py        # Deduplication tests
│   ├── test_metrics.py      # Metrics endpoint tests
│   ├── test_utils.py        # Username sanitization tests
│   └── locustfile.py        # Load testing
├── deploy/
│   ├── mist-userid-api.service
│   ├── mist-userid-worker.service
│   ├── nginx-mist-userid.conf
│   ├── env.example          # Template for /etc/mist-userid/env
│   └── zabbix/
│       ├── mist-userid.conf           # Zabbix UserParameter definitions
│       └── mist-userid-template.yaml  # Zabbix 6.0 importable template
├── Makefile                  # install, update, configure, deploy, test, zabbix
├── requirements.txt
├── requirements-dev.txt      # pytest, locust, etc.
├── CHANGELOG.md
├── LICENSE                   # MIT
├── SPEC.md                   # Full technical specification
└── README.md                 # User-facing documentation
```
