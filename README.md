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

### Multi-Target Example

```bash
# Single firewall (dev/test)
PA_TARGETS=https://pa-fw1.example.com

# Multiple firewalls
PA_TARGETS=https://pa-fw1.example.com,https://pa-fw2.example.com

# Panorama (production — redistributes to managed firewalls)
PA_TARGETS=https://panorama.example.com
```

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

### Memory usage growing
- Check `systemctl status mist-userid-worker` for memory stats
- The systemd `MemoryMax` will kill and restart the process if it exceeds limits
- Normal memory usage should be well under 100M

## Future: High Availability (F5)

Production plan: two instances behind an F5 load balancer for zero-downtime patching.

- **API**: Stateless — F5 round-robins between both boxes, no session affinity needed
- **Worker**: Safe to run on both boxes — Redis `BRPOP` is atomic, each event consumed by exactly one worker
- **Redis**: Must be shared between both boxes (dedicated Redis host or networked Redis with sentinel)
- **TLS**: F5 terminates TLS, backends use HTTP on port 8000

When ready, change `REDIS_URL` on both boxes to point to the shared Redis host and remove the local nginx proxy.

## License

MIT License. See [LICENSE](LICENSE) for details.
