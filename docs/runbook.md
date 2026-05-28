# Mist User-ID — Operations Runbook

**Service:** mist-userid (API + Worker)
**Host:** mist-userid-01
**Config:** `/etc/mist-userid/env`
**Deployed:** `/opt/mist-userid/`

---

## Quick Reference

### Service Commands
```bash
# Status
sudo systemctl status mist-userid-api mist-userid-worker

# Restart
sudo systemctl restart mist-userid-api
sudo systemctl restart mist-userid-worker

# Logs (live)
sudo journalctl -u mist-userid-api -u mist-userid-worker -f

# Logs (last hour, errors only)
sudo journalctl -u mist-userid-api -u mist-userid-worker --since "1 hour ago" -p warning
```

### Queue Commands
```bash
# Queue depth (should be near 0)
redis-cli LLEN userid_queue

# DLQ depth (should be 0)
redis-cli LLEN userid_dlq

# Inspect DLQ entries
redis-cli LRANGE userid_dlq 0 -1 | python3 -c "
import sys, json, time
for line in sys.stdin:
    d = json.loads(line)
    age = int(time.time() - d['timestamp'])
    print(f\"Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d['timestamp']))} ({age//3600}h {(age%3600)//60}m ago)\")
    print(f\"Targets: {d['targets']}\")
    print(f\"Logins: {len(d.get('logins',[]))}, Logouts: {len(d.get('logouts',[]))}\")
    print(f\"Error: {d.get('error','')}\")
    print()
"

# Clear DLQ
redis-cli DEL userid_dlq
```

### Stale Event Analysis
```bash
# Count stale events by building/AP (last 24 hrs)
sudo journalctl -u mist-userid-api --since "24 hours ago" 2>/dev/null \
  | grep "stale event" \
  | grep -oP "site=\S+ ap=\S+ ssid=\S+" \
  | sort | uniq -c | sort -rn | head -20

# Count total stale events today
sudo journalctl -u mist-userid-api --since "today" 2>/dev/null \
  | grep -c "stale event"
```

### Health Endpoints
```bash
curl -s http://localhost/health | python3 -m json.tool
curl -s http://localhost/ready  | python3 -m json.tool
curl -s http://localhost/metrics | grep -E "^mist_"
```

---

## Runbook Procedures

---

### 1. DLQ Has Entries

**Symptom:** `redis-cli LLEN userid_dlq` returns > 0, or Zabbix fires DLQ alert.

**What it means:** The worker exhausted all 5 retries sending a batch to one or more PA firewalls. The affected user-to-IP mappings were not registered with PA.

**Targets:** `pa-fw-01.mgmt.example.edu`, `pa-fw-02.mgmt.example.edu`

**Steps:**

1. Inspect the DLQ to identify which target(s) failed and when:
   ```bash
   redis-cli LRANGE userid_dlq 0 -1 | python3 -c "
   import sys, json, time
   for line in sys.stdin:
       d = json.loads(line)
       age = int(time.time() - d['timestamp'])
       print(f\"{time.strftime('%Y-%m-%d %H:%M', time.localtime(d['timestamp']))} — {age//60}m ago — targets: {d['targets']} — logins: {len(d.get('logins',[]))}\")
   "
   ```

2. Test PA connectivity manually:
   ```bash
   curl -sk "https://pa-fw-01.mgmt.example.edu/api/?type=version" | head -5
   curl -sk "https://pa-fw-02.mgmt.example.edu/api/?type=version" | head -5
   ```
   Expected: XML response with PAN-OS version. Timeout or error = connectivity issue.

3. If PA is reachable now, the DLQ entries represent a past outage window. The affected users will self-heal when they next roam or reconnect (generating a fresh event). Clear the DLQ:
   ```bash
   redis-cli DEL userid_dlq
   ```

4. If PA is still unreachable, escalate to the network team. Do NOT clear the DLQ yet — entries are evidence of the outage window.

5. **pan03 specifically** has had 3 isolated failures (Feb 28, Mar 5, Mar 6 2026). Flag to network team as a recurring issue on the `pan03-uap-mgmt` management path.

**Impact:** Affected users may not match firewall policy by username until they roam or reconnect. AD User-ID covers wired users regardless.

---

### 2. Stale Event Spike

**Symptom:** Large number of `Skipping stale event` log entries; `EVENTS_REJECTED{reason="stale_event"}` metric spikes.

**Normal baseline:** 0–5 stale events per day (natural Mist batching edge cases).

**Alert threshold:** > 50 stale events/hour warrants investigation.

**What it means:** One of two causes:

| Pattern | Cause |
|---|---|
| Events 40–70 min old, concentrated in 1–2 buildings | Mist retrying a backlog from a previous PA outage |
| Events 5–15 min old, spread across all buildings | Mist's natural batching delay exceeding `WEBHOOK_MAX_AGE` |

**Steps:**

1. Identify which buildings are affected:
   ```bash
   sudo journalctl -u mist-userid-api --since "1 hour ago" 2>/dev/null \
     | grep "stale event" \
     | grep -oP "site=\S+ ap=\S+ ssid=\S+" \
     | sort | uniq -c | sort -rn | head -20
   ```

2. Check event ages:
   ```bash
   sudo journalctl -u mist-userid-api --since "1 hour ago" 2>/dev/null \
     | grep "stale event" \
     | grep -oP "age=\K[0-9]+" \
     | awk '{sum+=$1; count++; if($1>max)max=$1} END {print "Count:", count, "Avg:", sum/count"s", "Max:", max"s"}'
   ```

3. **If events are 40–70 min old** (Mist retry backlog):
   - Check DLQ for a recent PA outage that triggered Mist retries
   - The backlog will drain on its own over 30–60 minutes
   - If volume is excessive (thousands/hour), disable the Mist webhook briefly to drain the retry queue, then re-enable:
     - Mist Portal → Organization → Webhooks → disable → wait for queue to reach 0 req/min → re-enable

4. **If events are 5–15 min old** (batching delay):
   - Consider bumping `WEBHOOK_MAX_AGE` in `/etc/mist-userid/env` (current: 300s)
   - Restart API after change: `sudo systemctl restart mist-userid-api`

---

### 3. PA Authentication Failure / API Key Expired

**Symptom:** Worker logs show `401` responses from PA, or `Regenerated API key after session timeout`.

**What it means:** The PA API session expired. This is normal and handled automatically — the worker detects the 401, calls the keygen API with `PA_USERNAME`/`PA_PASSWORD`, refreshes the key in memory, and retries.

**Steps:**

1. Check worker logs for the auto-refresh message:
   ```bash
   sudo journalctl -u mist-userid-worker --since "1 hour ago" 2>/dev/null \
     | grep -i "api key\|401\|auth\|keygen"
   ```

2. If auto-refresh succeeded (log shows "Regenerated API key"), no action needed.

3. If auto-refresh failed repeatedly and entries are landing in DLQ:
   - Verify PA credentials in `/etc/mist-userid/env` (`PA_USERNAME`, `PA_PASSWORD`)
   - Test keygen manually:
     ```bash
     curl -sk "https://pa-fw-01.mgmt.example.edu/api/?type=keygen&user=<user>&password=<pass>"
     ```
   - If credentials are wrong, update `/etc/mist-userid/env` and restart the worker

---

### 4. Mist Webhook Reset

**When to use:** Stale event storm that isn't draining, or after a prolonged outage where Mist has built up a large retry backlog.

**What happens:** Disabling the webhook in Mist stops all delivery attempts. Re-enabling starts fresh — Mist does not replay the backlog from before the disable.

**Steps:**

1. Log into Mist Portal → **Organization** → **Webhooks**
2. Disable the webhook for the affected org/site
3. Monitor logs until traffic drops to 0:
   ```bash
   sudo journalctl -u mist-userid-api -f 2>/dev/null | grep "POST /mist/webhook"
   ```
4. Wait ~2 minutes for in-flight requests to complete
5. Re-enable the webhook in Mist Portal
6. Verify fresh traffic is coming in and stale events are gone:
   ```bash
   sudo journalctl -u mist-userid-api --since "2 minutes ago" 2>/dev/null \
     | grep -E "Queued|stale"
   ```

**Note:** Users connected at the time of reset will not re-register until they roam or reconnect. This is acceptable — their sessions were already established.

---

### 5. Building Onboarding (Adding a New Site to Webhook)

**What happens:** When a new building is added to the Mist webhook scope, only new events (connects/roams/disconnects) are received going forward. Users already connected at the moment of activation are unknown to PA until they roam or reconnect.

**Steps:**

1. In Mist Portal, add the new site to the webhook subscription
2. Verify events are flowing in from the new site:
   ```bash
   sudo journalctl -u mist-userid-api -f 2>/dev/null \
     | grep "site=<NewSiteName>"
   ```
3. Accept that currently-connected users will self-heal within a few minutes as they naturally generate events (roaming, reconnecting, etc.)
4. Peak time to onboard: morning before users arrive, so the initial rush of connects populates PA naturally

**Future:** A bulk onboarding script (Roadmap) will query the Mist API for all connected clients at a site and pre-register them with PA at activation time.

---

### 6. Service Won't Start

**Steps:**

1. Check status and recent logs:
   ```bash
   sudo systemctl status mist-userid-api
   sudo journalctl -u mist-userid-api --since "5 minutes ago" 2>/dev/null | tail -30
   ```

2. Common causes:
   - **Config error:** Missing or invalid value in `/etc/mist-userid/env` → fix the env file, restart
   - **Redis not running:** `sudo systemctl status redis` → `sudo systemctl start redis`
   - **Port conflict:** Another process on port 8000 → `sudo ss -tlnp | grep 8000`
   - **SELinux denial:** `sudo ausearch -m avc -ts recent | tail -20`
   - **Python/venv issue:** `sudo /opt/mist-userid/venv/bin/python -c "from app.main import app"` to test import

3. After fixing, restart and verify:
   ```bash
   sudo systemctl restart mist-userid-api
   curl -s http://localhost/health
   ```

---

### 7. PA SSL Certificate Expired

**Symptom:** Worker logs show `CERTIFICATE_VERIFY_FAILED: certificate has expired`, worker watchdog crash (systemd reports `Failed with result 'watchdog'`), DLQ accumulating rapidly.

**Check cert expiry on both targets:**
```bash
echo | openssl s_client -connect pa-fw-01.mgmt.example.edu:443 2>/dev/null | openssl x509 -noout -dates
echo | openssl s_client -connect pa-fw-02.mgmt.example.edu:443 2>/dev/null | openssl x509 -noout -dates
```

**Steps:**
1. Escalate cert renewal to whoever manages the PA firewalls
2. While cert is being renewed, pan04 handles all traffic (worker falls back automatically)
3. After cert renewed, restart the worker to clear the SSL error state:
   ```bash
   sudo systemctl restart mist-userid-worker
   ```
4. Verify both targets responding:
   ```bash
   sudo journalctl -u mist-userid-worker -f 2>/dev/null | grep "HTTP Request"
   ```
5. Clear DLQ accumulated during outage: `redis-cli DEL userid_dlq`

**Prevention:** Both PA certs are on annual cycles, both now expiring Dec 12, 2026. Set a calendar reminder for **~Nov 12, 2026** to renew both before expiry. After renewal, both will likely align to the same date again.

---

### 8. High Queue Depth

**Symptom:** `redis-cli LLEN userid_queue` returns a large number (thousands); service returning 429.

**What it means:** The worker is not keeping up with incoming events, or the worker is down.

**Steps:**

1. Check if the worker is running:
   ```bash
   sudo systemctl status mist-userid-worker
   ```

2. If worker is down, restart it:
   ```bash
   sudo systemctl restart mist-userid-worker
   ```

3. If worker is running but queue isn't draining, check for PA connectivity issues (worker may be stuck retrying):
   ```bash
   sudo journalctl -u mist-userid-worker --since "10 minutes ago" 2>/dev/null | tail -30
   ```

4. Queue should drain within minutes once the worker is healthy.

---

## Key Configuration Reference

| Setting | Location | Current Value | Notes |
|---|---|---|---|
| `USERID_TIMEOUT` | `/etc/mist-userid/env` | 360 min | PA mapping lifetime; set to match shortest DHCP lease (faculty/staff = 2hr; students = 16hr; 6hr is compromise) |
| `WEBHOOK_MAX_AGE` | `/etc/mist-userid/env` | 300s | Reject events older than this; Mist natural batching can be 5–13 min |
| `MAX_QUEUE_DEPTH` | `/etc/mist-userid/env` | 10000 | Return 429 when Redis queue exceeds this |
| `BATCH_SIZE` | `/etc/mist-userid/env` | 50 | Events per PA API call |
| `PA_TARGETS` | `/etc/mist-userid/env` | pan03, pan04 | Comma-separated PA firewall URLs |
| `LOG_LEVEL` | `/etc/mist-userid/env` | INFO | Set DEBUG for investigation; auto-reverts via systemd timer |

---

## Known Issues & History

| Date | Issue | Resolution |
|---|---|---|
| Feb 28, 2026 | pan03 + pan04 both failed simultaneously | Cleared DLQ; escalated to network team (shared path suspected) |
| Mar 4, 2026 | 29,087 stale events (spike from 760) | Mist retry backlog from PA outages; reset webhook to drain |
| Mar 5, 2026 | pan03 isolated failure, 3 logins lost | Cleared DLQ |
| Mar 6, 2026 | SELinux blocking rsyslogd → StellarCyber (port 5555) | `semanage port -a -t syslogd_port_t -p tcp 5555` |
| Mar 6, 2026 | NTP inactive | Enabled chronyd, pointed to ntp.example.edu |
| Mar 6, 2026 | userid_timeout too short (60 min) | Increased to 360 min |
| May 21–28, 2026 | pan03 SSL cert expired; daily DLQ failures, worker watchdog crash | Both pan03 + pan04 certs renewed (valid through Dec 12, 2026); worker restarted; DLQ cleared |
