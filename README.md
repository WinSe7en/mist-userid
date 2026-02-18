# Mist User-ID

High-throughput webhook receiver that maps Juniper Mist wireless usernames to IP addresses and pushes them to Palo Alto firewalls via the XML User-ID API.

Designed for campus networks with 10,000+ users and 100+ events/second peak capacity.

## Architecture

```
Mist Cloud                    Your Server                         PA Firewalls
───────────                   ──────────────                      ────────────
                              ┌──────────────┐
  client-join  ──────────────▶│  FastAPI API  │
  client-sessions ───────────▶│  (uvicorn)   │
                              └──────┬───────┘
                                     │ Redis LPUSH
                              ┌──────▼───────┐
                              │  Redis Queue  │
                              │  + Dedup Cache│
                              └──────┬───────┘
                                     │ BRPOP
                              ┌──────▼───────┐     XML User-ID API
                              │    Worker     │────────────────────▶ PA-5410 / Panorama
                              │  (batching)   │
                              └──────────────┘
```

- **API receiver**: validates webhook signatures, filters events, queues to Redis, returns 202 immediately
- **Worker process**: consumes queue, deduplicates, batches login/logout entries, sends to PA targets with retry
- **Redis**: event queue + deduplication cache (all state lives here; processes are stateless)

## Prerequisites

- Python 3.9+
- Redis server — serves as both the event queue (decouples the API from the worker) and the deduplication cache (prevents repeated User-ID updates for the same user+IP within the TTL window). Install with `sudo dnf install redis && sudo systemctl enable --now redis`
- RHEL 9 (or compatible Linux with systemd)
- Juniper Mist site with 802.1X (eduroam) or PSK wireless
- Palo Alto firewall or Panorama with API access

## Quick Start

```bash
# Clone the repository
git clone https://github.com/WinSe7en/mist-userid.git
cd mist-userid

# Install
sudo make install

# Configure (creates /etc/mist-userid/env from template)
sudo make configure
sudo vim /etc/mist-userid/env   # Set PA_TARGETS, credentials, MIST_WEBHOOK_SECRET

# Deploy (installs systemd services and starts them)
sudo make deploy

# Verify
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

## Mist Webhook Setup

1. In the Mist portal, navigate to **Organization > Site Configuration** > select your site
2. Under **Webhooks**, add a new webhook:
   - **Name**: `userid-mapper`
   - **Type**: HTTP Post
   - **URL**: `https://your-server:8000/mist/webhook`
   - **Secret**: a strong random string (same value as `MIST_WEBHOOK_SECRET` in your env file)
   - **Topics**: `client-sessions`, `client-join`
   - **Enabled**: Yes
3. Save the webhook configuration

The receiver uses the `client_username` field (802.1X identity) or `psk_name` field (PSK credential name) along with `client_ip` to create User-ID mappings.

### How Events Map to PA Actions

| Source | Condition | PA Action |
|--------|-----------|-----------|
| `client-join` | username + IP present | Login (initial connect) |
| `client-sessions` | `next_ap` is a real MAC | Login (roam refresh) |
| `client-sessions` | `next_ap == "000000000000"` | Logout (disconnect) |

## Palo Alto Authentication

You have two options for authenticating with the PA firewall:

### Option 1: API Key (static)

1. Log into your PA firewall or Panorama
2. Navigate to **Device > Administrators** (or use an existing service account)
3. Go to **Device > API Keys** and generate a key for the service account
4. The key needs permission to use the User-ID XML API (`/api/?type=user-id`)
5. Set the key as `PA_API_KEY` in `/etc/mist-userid/env`

### Option 2: Admin Credentials (auto-generate key)

Instead of a static API key, you can provide admin credentials and the service will automatically generate an API key at startup:

1. Create a service account on the PA firewall with XML API permissions
2. Set `PA_USERNAME` and `PA_PASSWORD` in `/etc/mist-userid/env`
3. Leave `PA_API_KEY` unset or empty

The key is generated once at startup, cached in memory, and auto-refreshes if it becomes invalid (e.g., after a password change). This is useful for environments where API keys shouldn't be stored in config files.

## Configuration Reference

All configuration is via environment variables (set in `/etc/mist-userid/env`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PA_TARGETS` | Yes | - | Comma-separated PA firewall/Panorama URLs |
| `PA_API_KEY` | Cond. | - | API key for PA XML API (required if username/password not set) |
| `PA_USERNAME` | Cond. | - | PA admin username for auto-generating API key |
| `PA_PASSWORD` | Cond. | - | PA admin password for auto-generating API key |
| `MIST_WEBHOOK_SECRET` | Yes | - | Shared secret for webhook HMAC validation |
| `REDIS_URL` | No | `redis://localhost:6379` | Redis connection string |
| `BATCH_SIZE` | No | `50` | Max items per PA API batch |
| `BATCH_FLUSH_INTERVAL` | No | `2` | Seconds between batch flushes |
| `DEDUP_TTL` | No | `300` | Dedup cache TTL in seconds |
| `MAX_RETRY_ATTEMPTS` | No | `5` | PA API retry limit |
| `USERID_TIMEOUT` | No | `60` | PA User-ID timeout in minutes (align with DHCP lease) |
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `LOG_FORMAT` | No | `text` | Log format: `text` or `json` |
| `IGNORE_SSIDS` | No | *(empty)* | Comma-separated SSIDs to ignore (case-insensitive) |
| `MAX_QUEUE_DEPTH` | No | `10000` | Reject webhooks with 429 when queue reaches this depth |
| `WEBHOOK_MAX_AGE` | No | `300` | Reject events with timestamps older than this many seconds |

### Multi-Target Example

```bash
# Single firewall (dev/test)
PA_TARGETS=https://pa-fw1.example.com

# Multiple firewalls (HA pair — redundant but resilient)
PA_TARGETS=https://pa-fw1.example.com,https://pa-fw2.example.com

# Panorama (recommended for multi-site)
PA_TARGETS=https://panorama.example.com
```

### Using Panorama

For environments with multiple sites or many firewall pairs, Panorama is the recommended target. The User-ID XML API is identical — no code changes required, just point `PA_TARGETS` to Panorama.

**Panorama configuration required:**

1. **Enable User-ID Redistribution**:
   - Navigate to `Panorama > Device Groups > [your-group] > Settings`
   - Under `User Identification`, enable redistribution to member firewalls

2. **Service account permissions**:
   - The admin account needs XML API access on Panorama
   - Role should include `User-ID Agent` permissions or equivalent

3. **Device Group scope**:
   - Mappings sent to Panorama are redistributed to all firewalls in the device group
   - Ensure your target firewalls are in a device group with redistribution enabled

**Benefits of Panorama:**
- Single API call redistributes to all managed firewalls
- Centralized User-ID management
- Scales better than direct firewall connections
- Survives individual firewall maintenance

**HA pair vs. Panorama:**

| Scenario | Recommendation |
|----------|----------------|
| Single site, one HA pair | Direct to both firewalls (current setup) |
| Single site, multiple pairs | Panorama |
| Multi-site | Panorama |
| No Panorama license | Direct to firewalls |

## systemd Service Management

```bash
# Check status
make status

# View logs (both services, follow mode)
make logs

# Restart after config change
make restart

# Stop services
make stop

# Start services
make start

# Remove everything
sudo make clean
```

### Service Details

| Service | Description | Memory Limit |
|---------|-------------|--------------|
| `mist-userid-api` | FastAPI webhook receiver (4 uvicorn workers) | 512M |
| `mist-userid-worker` | Queue consumer + PA API sender | 256M |

Both services:
- Auto-restart on crash (`Restart=always`)
- Watchdog timeout at 30s (catches hangs)
- Security hardened (`NoNewPrivileges`, read-only filesystem)
- Environment from `/etc/mist-userid/env`

## Health Checks & Metrics

| Endpoint | Purpose | Success |
|----------|---------|---------|
| `GET /health` | Liveness — app is running | `{"status": "ok"}` |
| `GET /ready` | Readiness — Redis + PA targets reachable | `{"status": "ready", "targets": {...}}` |
| `GET /metrics` | Prometheus metrics (text format) | Counters, histograms, gauges |

## Monitoring

The API service exposes metrics via the `/metrics` endpoint in Prometheus format. You can integrate with either Prometheus or Zabbix.

### Available Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mist_userid_events_received_total` | Counter | `topic` | Webhook events received (by topic) |
| `mist_userid_events_queued_total` | Counter | - | Events added to Redis queue |
| `mist_userid_events_rejected_total` | Counter | `reason` | Rejected events (no_username, no_ip, ignored_ssid) |
| `mist_userid_events_deduped_total` | Counter | - | Duplicate events skipped by cache |
| `mist_userid_dlq_events_total` | Counter | - | Events moved to dead-letter queue |
| `mist_userid_queue_depth` | Gauge | - | Current Redis queue size |

### Prometheus

Add this scrape config to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'mist-userid'
    static_configs:
      - targets: ['your-server:8000']
    metrics_path: /metrics
    scrape_interval: 30s
```

### Zabbix

A Zabbix template and UserParameter config are provided for Zabbix 6.0+.

**Install UserParameters on the monitored host:**

```bash
sudo make zabbix
```

This copies the config to `/etc/zabbix/zabbix_agentd.d/` (or `zabbix_agent2.d/`) and restarts the agent.

**Import the template into Zabbix:**

1. Go to **Configuration > Templates > Import**
2. Select `deploy/zabbix/mist-userid-template.yaml`
3. Link the template to your host

**Included in the template:**

| Category | Items |
|----------|-------|
| Health | API health, Worker health |
| Queues | Queue depth, DLQ depth, Dedup cache size |
| Events | Received, queued, rejected, deduped (totals + rates) |
| Memory | API service memory, Worker service memory |

**Triggers:**

| Trigger | Severity |
|---------|----------|
| API is down | High |
| Worker is down | High |
| Queue depth > 100 | Warning |
| Queue depth > 500 | High |
| DLQ has failed events | Warning |
| API memory > 450MB | Warning |
| Worker memory > 220MB | Warning |
| No events for 10 minutes | Info |

**Graphs:**

- Event Throughput (received vs processed rate)
- Queue Depth (main queue vs DLQ)
- Memory Usage (API vs Worker)
- Event Totals (received, processed, deduped, rejected)

## Logging

### Changing the Log Level

Edit `/etc/mist-userid/env` and restart the affected service:

```bash
# Set desired level
sudo sed -i 's/^LOG_LEVEL=.*/LOG_LEVEL=DEBUG/' /etc/mist-userid/env

# Restart (worker, API, or both)
sudo systemctl restart mist-userid-worker mist-userid-api

# View logs
journalctl -u mist-userid-worker -f
journalctl -u mist-userid-api -f
```

### Log Levels

| Level | When to Use | What You'll See |
|-------|-------------|-----------------|
| `ERROR` | Production (quiet) | PA API auth failures, DLQ writes, unexpected exceptions |
| `WARNING` | Production (default recommended) | Transient PA errors with retries, dead-lettered batches, invalid queue entries |
| `INFO` | Production (verbose) | Batch sends (target count, login/logout counts), service start/stop, PA API HTTP status |
| `DEBUG` | Troubleshooting only | Individual user+IP events, dedup hits/misses, XML payloads, SSID filtering, queue operations |

**Recommendation**: Run `INFO` in production. Switch to `DEBUG` temporarily when troubleshooting a specific user or verifying mappings, then switch back — DEBUG is noisy at high event rates.

### JSON Logging

For log aggregation (Splunk, ELK, etc.), switch to structured JSON output:

```bash
# In /etc/mist-userid/env
LOG_FORMAT=json
```

Each log line becomes a JSON object with `timestamp`, `level`, `logger`, and `message` fields.

### What Each Level Shows

**DEBUG** (most verbose):
```
Event: user=jsmith@example.edu ip=10.5.63.6 action=login topic=client-join next_ap=N/A
Dedup skip: user=jsmith@example.edu ip=10.5.63.6
Skipping event: ignored SSID=DU Guest WiFi
Flushing batch: 3 logins, 1 logouts (trigger: timer)
XML payload (245 bytes): <uid-message>...
```

**INFO**:
```
Sending batch to 1 targets: 3 logins, 1 logouts
Worker starting (batch_size=50, flush_interval=2.0s)
HTTP Request: POST https://pa-fw1.example.com/api/ "HTTP/1.1 200 OK"
```

**WARNING**:
```
Transient error 503 from https://pa-fw1.example.com, retry 1/5 in 1s
Dead-lettered batch (3 logins, 1 logouts) for targets: https://pa-fw1.example.com
```

**ERROR**:
```
Permanent auth failure from https://pa-fw1.example.com: 401
Max retries reached for https://pa-fw1.example.com (last status: 503)
Failed to write to DLQ: ConnectionError
```

## Dead-Letter Queue (DLQ)

Batches that fail after all retry attempts are written to a Redis list (`userid_dlq`) for inspection and potential manual retry.

### Inspecting the DLQ

```bash
# Count entries
redis-cli LLEN userid_dlq

# View recent failures
redis-cli LRANGE userid_dlq 0 4

# View failure timestamps (human-readable)
redis-cli LRANGE userid_dlq 0 -1 | grep -oP '"timestamp": \K[0-9.]+' | \
  xargs -I{} date -d @{} "+%Y-%m-%d %H:%M"
```

Each DLQ entry is JSON:
```json
{
  "timestamp": 1769443310.81,
  "targets": ["https://pa-fw1.example.com"],
  "logins": [["user@example.edu", "10.5.1.1"]],
  "logouts": [["user2@example.edu", "10.5.1.2"]],
  "error": "All retries exhausted for targets: https://pa-fw1.example.com"
}
```

### When to Retry

- **Recent failures (< 5 minutes)**: Worth retrying — the user-IP mappings are still valid
- **Stale failures (hours/days old)**: Usually not worth retrying — IPs may have been reassigned via DHCP, and logout targets may have already timed out on the PA

### Manual Retry Script

Save as `/opt/mist-userid/retry_dlq.py`:

```python
import asyncio, json, redis, httpx, os
from xml.etree.ElementTree import Element, SubElement, tostring

def build_xml(logins, logouts, timeout=60):
    msg = Element("uid-message")
    SubElement(msg, "type").text = "update"
    payload = SubElement(msg, "payload")
    if logins:
        el = SubElement(payload, "login")
        for user, ip in logins:
            SubElement(el, "entry", name=user, ip=ip, timeout=str(timeout))
    if logouts:
        el = SubElement(payload, "logout")
        for user, ip in logouts:
            SubElement(el, "entry", name=user, ip=ip)
    return tostring(msg, encoding="unicode")

async def retry():
    r = redis.Redis(decode_responses=True)
    print(f"DLQ has {r.llen('userid_dlq')} entries")
    ok = fail = 0
    async with httpx.AsyncClient(timeout=30, verify=True) as c:
        while (entry := r.rpop("userid_dlq")):
            d = json.loads(entry)
            xml = build_xml(d.get("logins",[]), d.get("logouts",[]))
            for target in d["targets"]:
                try:
                    resp = await c.post(f"{target}/api/",
                        data={"type":"user-id","key":os.environ["PA_API_KEY"],"cmd":xml})
                    if "success" in resp.text: ok += 1
                    else: fail += 1; print(f"✗ {target}: {resp.text[:80]}")
                except Exception as e: fail += 1; print(f"✗ {target}: {e}")
    print(f"Done: {ok} ok, {fail} failed")

asyncio.run(retry())
```

Run with:
```bash
sudo bash -c 'export $(grep -v "^#" /etc/mist-userid/env | xargs) && \
  /opt/mist-userid/venv/bin/python /opt/mist-userid/retry_dlq.py'
```

### Clearing the DLQ

If entries are too stale to retry:
```bash
redis-cli DEL userid_dlq
```

## Development

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run API locally (uses env vars or .env file)
uvicorn app.main:app --reload

# Run worker locally
python -m app.worker

# Run tests
pytest -v

# Run specific test file
pytest tests/test_webhook.py -v
```

## How It Works

1. Mist sends a webhook POST with `X-Mist-Signature-v2` HMAC-SHA256 header
2. API validates the signature, extracts `client_username`/`psk_name` + `client_ip`
3. Valid events are JSON-serialized and pushed to a Redis list (`userid_queue`)
4. Worker BRPOPs events, checks the Redis dedup cache (5-min TTL)
5. Events are classified as login or logout based on `next_ap` field
6. When batch reaches 50 items or 2 seconds elapse, worker builds XML and POSTs to PA targets
7. Failed batches retry with exponential backoff (1s, 2s, 4s, 8s, 16s)

### PA XML Format Sent

```xml
<uid-message>
  <type>update</type>
  <payload>
    <login>
      <entry name="user@example.edu" ip="10.7.71.140" timeout="60"/>
    </login>
    <logout>
      <entry name="user2@example.edu" ip="10.7.71.141"/>
    </logout>
  </payload>
</uid-message>
```

## Scaling & Performance

This service is designed for 10,000+ users at 100+ events/second. As buildings are added to Mist coverage, use the metrics below to stay ahead of capacity limits.

### Metrics to Watch During Rollout

```bash
# Queue depth — should be 0 or near 0 at steady state
redis-cli LLEN userid_queue

# Events queued per second (watch this climb after each building goes live)
curl -s http://localhost:8000/metrics | grep events_queued_total

# Events rejected (stale/invalid) — a spike here indicates a configuration problem
curl -s http://localhost:8000/metrics | grep events_rejected_total

# Queue-full rejections — if this is non-zero, the worker can't keep up with the webhook rate
curl -s http://localhost:8000/metrics | grep webhook_queue_full_total

# PA API latency (p99 latency climbing = PA is under load or WAN issues)
curl -s http://localhost:8000/metrics | grep pa_request_duration
```

### Capacity Thresholds

| Signal | Healthy | Investigate | Action |
|--------|---------|-------------|--------|
| Queue depth | 0–10 | 10–500 | >500: worker falling behind |
| `webhook_queue_full_total` | 0 | Any | Worker can't drain queue; see below |
| PA request latency (p99) | <1s | 1–5s | >5s: PA overloaded or WAN issue |
| API memory | <300MB | 300–400MB | >400MB: reduce `--workers` count |
| Worker memory | <150MB | 150–200MB | >200MB: check for batch accumulation |

### What Happens at Scale

**At current scale (~1 building):** The architecture has significant headroom. A typical Mist campus event arrives at 1–10 events/sec per building. The worker drains the queue faster than events arrive.

**As buildings are added:** Each building adds roughly proportional event volume. The bottleneck order is:
1. **PA API throughput** — batch size (50 events) and flush interval (2s) control how many XML requests/sec go to PA. If PA is slow, the worker accumulates a backlog.
2. **Redis queue depth** — if PA is slow for >5 minutes, the queue grows. Monitor `userid_queue` depth.
3. **Webhook receiver throughput** — uvicorn with 4 workers handles thousands of requests/sec. This is unlikely to be the bottleneck.

### Known Optimization Opportunities

These are not needed at current scale but are documented for when load grows:

**1. Redis pipeline for batch pushes (most impactful)**

Currently each queued event is a separate `LPUSH` Redis call. At high event rates, this adds per-event round-trip overhead (~0.1ms each on localhost). When this becomes a bottleneck, replace the per-event `lpush` calls in `app/webhook.py` with a Redis pipeline:

```python
async with r.pipeline() as pipe:
    for serialized_event in valid_events:
        pipe.lpush(QUEUE_KEY, serialized_event)
    await pipe.execute()
```

This collapses N Redis calls per webhook into 1 round trip. Implement this if webhook handler latency climbs above ~50ms under load.

**2. Capture `time.time()` once per webhook (minor)**

`is_fresh_event()` calls `time.time()` once per event. For a 50-event webhook this is 50 syscalls when 1 would do. Capture `now = time.time()` before the event loop and pass it in. Negligible until very high event rates.

**3. Cache `ignore_ssid_set` on the Settings object (minor)**

`settings.ignore_ssid_set` is a `@property` that rebuilds the set on every access. In the event loop that's one rebuild per event. If you have many ignored SSIDs and high event volume, caching the result as a private attribute would help. Not measurable at current scale.

### When to Increase `--workers`

The API service runs `uvicorn` with `--workers 4`. Each worker handles webhook validation and Redis pushes. Since the work is I/O-bound (Redis writes), 4 workers is generous for current load. If you see uvicorn CPU usage consistently above 80% on all 4 workers, increase to 8. Match to available cores.

```bash
# Check current CPU per worker
ps aux | grep uvicorn

# Edit worker count in service file
sudo vi /etc/systemd/system/mist-userid-api.service
# Change: --workers 4  →  --workers 8
sudo systemctl daemon-reload && sudo systemctl restart mist-userid-api
```

## Troubleshooting

### Webhook not receiving events
- Verify the webhook URL is reachable from the Mist cloud
- Check that `client-sessions` and `client-join` topics are subscribed
- Verify the secret matches between Mist config and `MIST_WEBHOOK_SECRET`
- Check API logs: `journalctl -u mist-userid-api -f`

### Events queued but not sent to PA
- Check worker logs: `journalctl -u mist-userid-worker -f`
- Verify `PA_TARGETS` URLs are reachable from the server
- Verify `PA_API_KEY` is valid (check for 401/403 errors in logs)
- Check Redis queue depth: `redis-cli LLEN userid_queue`

### High dedup rate (most events skipped)
- This is normal — the same user+IP pair won't be re-sent within 5 minutes
- Adjust `DEDUP_TTL` if you need more frequent updates

### SELinux denials

The `make deploy` target runs `make selinux` automatically, which configures port contexts, file contexts, and network booleans. If you still see issues:

```bash
# Check for recent AVC denials
sudo ausearch -m avc -ts recent

# Verify the services are running in the expected domain
ps -eZ | grep mist-userid

# Check port 8000 is labeled correctly
sudo semanage port -l | grep 8000

# Check file contexts on the venv
ls -Z /opt/mist-userid/venv/bin/python

# Verify network boolean is set
getsebool httpd_can_network_connect
```

If denials persist, generate and install a targeted policy module:
```bash
sudo ausearch -m avc -ts recent | audit2allow -M mist-userid
sudo semodule -i mist-userid.pp
```

To re-run SELinux setup after changes:
```bash
sudo make selinux
```

### Firewall (firewalld)

Port 8000/tcp is opened automatically by `make deploy`. To verify or manage manually:

```bash
# Check if port is open
sudo firewall-cmd --list-ports

# Open manually
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload

# Remove
sudo firewall-cmd --permanent --remove-port=8000/tcp
sudo firewall-cmd --reload
```

## Operations Quick Reference

Day-to-day commands for managing the service without any external tools.

### Full Health Check

Run all of these to get a complete picture of service health:

```bash
# 1. Service status (are both processes running?)
sudo systemctl status mist-userid-api --no-pager
sudo systemctl status mist-userid-worker --no-pager

# 2. Application health (is the API running? version?)
curl -s http://localhost:8000/health

# 3. Readiness (Redis + PA targets reachable, API key valid?)
curl -s http://localhost:8000/ready | python3 -m json.tool

# 4. Queue depth (should be 0 or near 0; >100 means worker is behind)
redis-cli LLEN userid_queue

# 5. Dead-letter queue (should be 0; >0 means failed batches need attention)
redis-cli LLEN userid_dlq

# 6. Memory usage (compare against limits: API 512MB, Worker 256MB)
systemctl show mist-userid-api --property=MemoryCurrent --value
systemctl show mist-userid-worker --property=MemoryCurrent --value

# 7. Recent errors (check for anything unexpected)
sudo journalctl -u mist-userid-worker --since "24 hours ago" | grep -iE "ERROR|WARNING" | tail -20
```

### Event Metrics

The API service exposes Prometheus-format counters at `/metrics`. These are cumulative since the API was last restarted.

```bash
# View all metrics
curl -s http://localhost:8000/metrics | grep "^mist_userid" | grep -v created

# Quick summary (human-readable)
curl -s http://localhost:8000/metrics | python3 -c "
import sys
for line in sys.stdin:
    if line.startswith('mist_userid') and 'created' not in line:
        parts = line.strip().split()
        name = parts[0].replace('mist_userid_','').replace('_total','')
        print(f'  {name:<40} {parts[1]}')
"
```

**What the metrics mean:**

| Metric | What It Tells You |
|--------|-------------------|
| `events_received{topic=...}` | Total webhooks received per topic (client-join, client-sessions) |
| `events_queued` | Events that passed filtering and were queued for the worker |
| `events_rejected{reason=...}` | Events filtered out: `no_username`, `no_ip`, `ignored_ssid`, `invalid_ip` |
| `events_deduped` | Duplicate user+IP pairs skipped (same mapping within 5min TTL) |
| `dlq_events` | Batches that failed all retries and were dead-lettered |

**Healthy system**: `received` >> `queued` (most events lack username/IP and are filtered), `dlq_events` = 0.

### DLQ Inspection & Diagnosis

The dead-letter queue (`userid_dlq`) stores batches that failed after all retries. Each entry is JSON with a timestamp, the affected targets, and the login/logout mappings.

```bash
# Count entries
redis-cli LLEN userid_dlq

# View entries with timestamps and summary
redis-cli LRANGE userid_dlq 0 -1 | python3 -c "
import sys, json
from datetime import datetime
for line in sys.stdin:
    d = json.loads(line.strip())
    dt = datetime.fromtimestamp(d['timestamp']).strftime('%a %b %d %H:%M')
    logins = len(d.get('logins', []))
    logouts = len(d.get('logouts', []))
    targets = ', '.join(d.get('targets', []))
    error = d.get('error', '')
    print(f'{dt}  {logins}L/{logouts}O  {targets}')
"

# View a single entry in full detail (first entry)
redis-cli LINDEX userid_dlq 0 | python3 -m json.tool

# Check logs around a DLQ timestamp for the actual error
# (replace the date/time with the DLQ entry timestamp)
sudo journalctl -u mist-userid-worker --since "2026-02-16 07:15" --until "2026-02-16 07:18"
```

**Diagnosing DLQ entries:**

| Log Error | Meaning | Action |
|-----------|---------|--------|
| `Commit-window 403` | PAN-OS was mid-commit (handled automatically since v0.2.1+) | Clear — service now retries these |
| `Permanent auth failure: 403` | Service account lacks User-ID permissions | Check PA admin role has XML API + User-ID Agent |
| `Permanent auth failure: 401` | API key invalid even after refresh | Check PA_USERNAME/PA_PASSWORD credentials |
| `Session expired (XML unauth)` | PA session timed out (handled automatically) | Clear — service auto-refreshes the key |
| `Max retries reached (status: 5xx)` | PA firewall temporarily unavailable | Check PA firewall health; entries may be stale |
| `Connection refused` / `Timeout` | Network issue to PA target | Check connectivity, firewall rules, PA is up |

**When to retry vs. clear:**
- **< 5 minutes old**: Might be worth retrying (user+IP still valid)
- **Hours/days old**: Clear them — IPs may have been reassigned via DHCP

```bash
# Clear all DLQ entries
redis-cli DEL userid_dlq

# Check active dedup cache entries
redis-cli KEYS 'dedup:*' | wc -l
```

### Logs

```bash
# Follow both services live
sudo journalctl -u mist-userid-api -u mist-userid-worker -f

# Check for errors in the last 24 hours
sudo journalctl -u mist-userid-worker --since "24 hours ago" | grep -iE "ERROR|WARNING"

# Check for PA auth issues specifically
sudo journalctl -u mist-userid-worker --since "24 hours ago" | grep -i "unauth\|session\|401\|403\|commit-window"

# See batch sends (how often and how large)
sudo journalctl -u mist-userid-worker --since "1 hour ago" | grep "Sending batch"

# Count errors vs successes in the last 24h
sudo journalctl -u mist-userid-worker --since "24 hours ago" | grep -c "HTTP/1.1 200 OK"
sudo journalctl -u mist-userid-worker --since "24 hours ago" | grep -c "ERROR"
```

### PA Firewall Verification

```bash
# On the PA firewall CLI, verify User-ID mappings are landing
show user ip-user-mapping all
show user ip-user-mapping all | match jsmith
show user ip-user-mapping all | match 10.5.

# Count total mappings
show user ip-user-mapping all | match "Total:"
```

### Deploying Code Updates

When code changes are made in the git repository:

```bash
# 1. Pull latest code
cd /home/matt.johnson.03/projects/mist-userid
git pull

# 2. Run tests
python3 -m pytest -v

# 3. Copy updated app files to production
sudo cp app/*.py /opt/mist-userid/app/

# 4. If systemd unit files changed (CapabilityBoundingSet, hardening directives, etc.)
sudo cp deploy/mist-userid-api.service deploy/mist-userid-worker.service /etc/systemd/system/
sudo systemctl daemon-reload

# 5. If nginx config changed (ACLs, location blocks, etc.)
sudo cp deploy/nginx-mist-userid.conf /etc/nginx/conf.d/mist-userid.conf
sudo nginx -t && sudo systemctl reload nginx

# 6. Restart affected service(s)
# - Changed webhook.py or main.py? Restart API only
sudo systemctl restart mist-userid-api

# - Changed paloalto.py, pa_auth.py, worker.py, dedup.py? Restart worker only
sudo systemctl restart mist-userid-worker

# - Changed config.py or metrics.py? Restart both
sudo systemctl restart mist-userid-api mist-userid-worker

# 7. Verify
curl -s http://localhost:8000/health
curl -s http://localhost:8000/ready | python3 -m json.tool
redis-cli LLEN userid_queue
redis-cli LLEN userid_dlq
```

**What changed → what to restart:**

| Files changed | Action |
|---------------|--------|
| `app/webhook.py`, `app/main.py` | Restart API |
| `app/worker.py`, `app/paloalto.py`, `app/pa_auth.py`, `app/dedup.py` | Restart worker |
| `app/config.py`, `app/metrics.py` | Restart both |
| `deploy/mist-userid-*.service` | `daemon-reload` + restart both |
| `deploy/nginx-mist-userid.conf` | `nginx -t && systemctl reload nginx` |
| `deploy/env.example` | No action (template only; edit `/etc/mist-userid/env` manually if needed) |

**Full redeploy** (new dependencies, systemd changes, etc.):

```bash
cd /home/matt.johnson.03/projects/mist-userid
sudo make update    # copies app/ files and updates pip packages
sudo make deploy    # full redeploy (systemd units, nginx, SELinux, firewall)
```

### Restarting After Config Changes

```bash
# Edit the env file
sudo vim /etc/mist-userid/env

# Restart both services to pick up changes
sudo systemctl restart mist-userid-api mist-userid-worker

# Verify
curl -s http://localhost:8000/ready | python3 -m json.tool
```

### Memory Usage Growing
- Check `systemctl status mist-userid-worker` for current memory
- Normal usage: API ~100MB, Worker ~40-50MB
- systemd `MemoryMax` kills and auto-restarts the process if limits are exceeded (API: 512MB, Worker: 256MB)
- If memory grows steadily, check queue depth — a backlog can cause batch accumulation

## PAN-OS API Behaviors

The service handles several non-obvious PAN-OS API behaviors automatically. No operator action needed — these are documented here for troubleshooting context.

### Session Timeout (HTTP 200 with XML `status="unauth"`)

PAN-OS returns HTTP 200 (not 401) when the API session expires. The response body contains `status="unauth" code="22"` with "Session timed out". The service detects this, regenerates the API key via keygen, and retries the request automatically.

**What you'd see in logs:**
```
WARNING: Session expired on https://pan03... (HTTP 200 but XML unauth)
INFO: Regenerated API key after session timeout, retrying https://pan03...
```

### Commit-Window 403

During PAN-OS auto-commits (or manual commits), the firewall temporarily returns HTTP 403 with "Type [user-id] not authorized for user role." This is transient — the user-id API role is briefly unavailable during the commit. The service retries with exponential backoff (1s, 2s, 4s...) until the commit completes.

**What you'd see in logs:**
```
WARNING: Commit-window 403 from https://pan03..., retry 1/5 in 1s
```

If you see persistent 403 errors (not during commits), check that the service account has User-ID Agent / XML API permissions on the PA.

### Benign Logout Failures ("Delete mapping failed")

When the service sends a `<logout>` for a user whose mapping already expired (DHCP lease changed, PA timeout elapsed), PAN-OS returns `status="error"` with "Delete mapping failed." This is harmless — the mapping was already gone. The service treats this as success and does not retry or dead-letter.

### API Key Auto-Refresh on 401

If the PA returns HTTP 401, the service invalidates the cached API key, regenerates via keygen API, and retries with the new key. This handles password rotations and PA key invalidations without service restart.

## Future: Wired NAC (Certificate Auth)

Add User-ID mappings for wired users authenticating via Mist Access Assurance with user certificates (EAP-TLS).

### Prerequisites (Mist side)

1. **Create a NAC policy rule** in Mist Access Assurance that matches certificate-based authentication and assigns the correct VLAN. Without this rule, all cert auth attempts hit the implicit deny. As of Feb 2026, `minis-radius-user` test attempts are being denied with:
   ```
   "No policy rules are hit, rejected by implicit deny"
   ```
2. **Webhook topics**: Enable `nac-accounting` and `nac-events` on the Mist webhook (already done)
3. **Test user**: Have a user authenticate via EAP-TLS with a user certificate and capture the resulting webhook payload

### What We Know So Far

**Webhook topics:**

| Topic | Volume | Purpose | Has `client_ip` |
|-------|--------|---------|-----------------|
| `nac-accounting` | ~35/min | Session lifecycle (START/UPDATE/STOP) | Sometimes |
| `nac-events` | ~1/min | Auth decisions (PERMIT/DENY) | No |

**Payload fields observed (MAB only so far):**

```json
{
  "auth_type": "mab",
  "type": "NAC_ACCOUNTING_UPDATE",
  "username": "020000000c06",
  "client_ip": "198.51.100.216",
  "mac": "020000000c06",
  "port_id": "mge-1/0/5",
  "nas_ip": "10.1.46.202",
  "device_mac": "020000000c05"
}
```

### What We Need to Determine

Once a user successfully authenticates with a certificate, capture the payload and verify:

1. **`auth_type` value** — expected to be `eap-tls` or `dot1x` (not `mab`)
2. **Username field** — does `username` contain the cert identity (UPN/email from SAN), or is it in a different field like `idp_username` or `cert_cn`?
3. **`client_ip` populated?** — wired clients get IP via DHCP after auth; may only appear in `nac-accounting` UPDATE events after the initial START
4. **Login/logout mapping** — `NAC_ACCOUNTING_START` = login, `NAC_ACCOUNTING_STOP` = logout

### Expected Code Changes

Assuming the payload carries a real username and IP:

- **`app/webhook.py`**: Add `nac-accounting` to `VALID_TOPICS`, extract username from the cert-specific field, filter on `auth_type != "mab"` to skip device MAB events
- **`app/worker.py`**: Map `NAC_ACCOUNTING_START` → login, `NAC_ACCOUNTING_STOP` → logout (similar to `client-sessions` with `next_ap`)
- **Filtering**: Skip events where `username` is a MAC address (MAB devices like phones, printers)

### Wired IP Ranges

Wired clients use the `198.51.100.x.x` range, distinct from wireless (`10.5.x.x`, `10.7.x.x`). Verify on the PA:

```bash
# Wireless mappings (existing)
show user ip-user-mapping all | match 10.5.

# Wired mappings (new — once implemented)
show user ip-user-mapping all | match 198.51.100.
```

## Future: High Availability (F5 + Two App Servers + Redis Server)

**Current state:** Single server (dev-prod) running API, worker, Redis, and nginx on one box. Acceptable for early rollout; not resilient to reboots or updates.

**Target state:** Three-server environment for zero-downtime patching and rolling reboots.

```
Mist Cloud
    │ HTTPS
    ▼
┌──────────────────────────────┐
│  F5 Load Balancer            │  TLS termination, health-check based routing
│  (VIP: webhook-vip.example.edu)   │
└──────────┬─────────────────┬─┘
           │                 │
    ┌──────▼──────┐   ┌──────▼──────┐
    │  App Server 1│   │  App Server 2│   Both run: FastAPI API + Worker
    │  (active)   │   │  (active)   │   No session affinity needed
    └──────┬───────┘   └──────┬──────┘
           │                  │
           └────────┬─────────┘
                    │ redis://redis-host:6379
              ┌─────▼──────┐
              │  Redis Host │   Shared queue + dedup cache
              │             │   (single source of truth)
              └─────────────┘
                    │
                    ▼
            PA Firewalls / Panorama
```

### Why This Works

**API (stateless):** The webhook handler validates the signature, checks queue depth, and pushes to Redis. No local state. F5 can round-robin freely between both app servers — no session affinity needed.

**Worker (safe to run on both boxes):** Redis `BRPOP` is atomic — each event is consumed by exactly one worker, whichever pops it first. Running two workers doubles throughput and means one can be stopped for maintenance while the other keeps draining the queue.

**Redis (shared state):** All coordination (event queue, dedup cache) lives in Redis. The app servers are interchangeable because they share the same Redis instance.

### Migration Steps

**Prerequisites:**
- Dedicated Redis host provisioned and accessible from both app servers
- F5 VIP configured with health-check monitor on `/health` (HTTP 200 = in service)
- Both app servers have the service installed and configured

**Step 1 — Set up the Redis host**
```bash
# On the Redis host
sudo dnf install redis
sudo systemctl enable --now redis

# Bind Redis to the management interface (not 0.0.0.0)
# Edit /etc/redis/redis.conf:
#   bind 127.0.0.1 <redis-host-mgmt-ip>
#   requirepass <strong-password>

sudo systemctl restart redis

# Verify from an app server
redis-cli -h <redis-host> -a <password> ping
```

**Step 2 — Update both app servers to point at shared Redis**
```bash
# On each app server, edit /etc/mist-userid/env:
REDIS_URL=redis://:<password>@<redis-host>:6379

sudo systemctl restart mist-userid-api mist-userid-worker
curl -s http://localhost:8000/ready  # should show redis: reachable
```

**Step 3 — Configure F5**
- VIP: existing public IP/hostname (`webhook-vip.example.edu`)
- Pool members: both app server IPs, port 443 (or 80 if F5 terminates TLS)
- Health monitor: HTTP GET `/health` → expect `200 OK` with `{"status": "ok"}`
- Load balancing: round-robin (no persistence/affinity needed)
- TLS: terminate on F5; backends communicate over HTTP port 8000

**Step 4 — Remove nginx from the equation (optional)**

With F5 doing TLS termination, nginx on the app servers becomes redundant. You can either:
- Keep nginx (provides local ACLs for `/ready` and `/metrics`) — recommended
- Remove nginx and have F5 proxy directly to uvicorn on port 8000

**Step 5 — Update Mist webhook URL**

The webhook URL stays the same (the F5 VIP hostname doesn't change). No Mist reconfiguration needed.

### Rolling Updates (Zero Downtime)

Once in the three-server state, deploy updates without dropping a single webhook:

```bash
# 1. Remove server 1 from F5 pool (or mark down in health check)
#    F5 routes all traffic to server 2

# 2. Update server 1
cd /home/matt.johnson.03/projects/mist-userid
git pull
sudo cp app/*.py /opt/mist-userid/app/
sudo systemctl restart mist-userid-api mist-userid-worker

# 3. Verify server 1 is healthy
curl -s http://server1:8000/health
curl -s http://server1:8000/ready

# 4. Return server 1 to F5 pool

# 5. Repeat for server 2
```

### Configuration Changes for HA

| Setting | Current (dev-prod) | HA Target |
|---------|-------------------|-----------|
| `REDIS_URL` | `redis://localhost:6379` | `redis://:<pass>@redis-host:6379` |
| nginx TLS | On each app server | F5 terminates; nginx optional |
| Mist webhook URL | `https://webhook-vip.example.edu/mist/webhook` | Same — no change |
| Firewall | Port 443 open to Mist cloud IPs | Same on F5 VIP; app servers only need port 8000 from F5 |

### Redis Auth in `/etc/mist-userid/env`

When Redis moves to a dedicated host with authentication:
```bash
# Format: redis://<user>:<password>@<host>:<port>/<db>
REDIS_URL=redis://:your-redis-password@redis.example.edu:6379
```

No code changes needed — `REDIS_URL` is passed directly to `aioredis`.

## License

MIT License. See [LICENSE](LICENSE) for details.
