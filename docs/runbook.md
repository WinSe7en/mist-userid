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

## Periodic Health Review Checklist

Run monthly (and after any reboot, PAN-OS upgrade, or cert change). Each item says what "healthy" looks like and what to do if it isn't.

> **Log retention caveat:** journald is persistent (since 2026-11-24, `deploy/journald-mist-userid.conf`) but capped at 1G, about 4–5 days at current volume. Before that it was RAM-only and held about 8 hours. For anything older, search `/var/log/messages*` (rotated weekly, ~4 weeks kept) instead of `journalctl`.

```bash
# Helper: all worker log history rsyslog still has
LOGS="$(ls /var/log/messages* | tr '\n' ' ')"
```

### A. Is it running?

| # | Check | Command | Healthy |
|---|-------|---------|---------|
| A1 | Both services active, no restarts | `systemctl show mist-userid-api mist-userid-worker -p ActiveState,NRestarts,ActiveEnterTimestamp` | `active`, `NRestarts=0`, start time = last reboot |
| A2 | No watchdog kills | `sudo grep -hciE "watchdog\|SIGABRT" $LOGS` | `0` |
| A3 | Readiness | `curl -s localhost:8000/ready` | `"ready"`, both targets `reachable`, `api_key` = `keygen` or `static` (auth mode only; `/ready` never generates keys, so key health shows in the worker log: "API key validated" at startup, no "Permanent auth failure") |
| A4 | Last reboot / kernel | `last reboot \| head -3` | Expected patch-window reboot; services came back on their own |
| A5 | Redis running | `systemctl show redis -p ActiveState,NRestarts,UnitFileState`; `redis-cli ping` | `active`, `NRestarts=0`, `enabled`, `PONG`. If down, the API returns errors to Mist and the worker backs off every 5s (procedure 6) |

### B. Is it keeping up?

| # | Check | Command | Healthy |
|---|-------|---------|---------|
| B1 | Queue depth | `redis-cli LLEN userid_queue` | `0` (brief spikes <100 fine) |
| B2 | Webhook volume (24h) | `sudo journalctl -u mist-userid-api --since "24 hours ago" -q \| grep -c "POST /mist/webhook"` | ~190–200K/day (Nov 2026 baseline); big drop = Mist webhook problem (procedure 4) |
| B3 | Stale / rejected events | `sudo grep -hc "stale event" $LOGS`; `sudo grep -hci "queue full\|signature validation failed" $LOGS` | All `0` |
| B4 | Memory vs limits | `systemctl show mist-userid-api mist-userid-worker -p MemoryCurrent,MemoryPeak` | API ~230–250MB (limit 512M), worker ~75–90MB (limit 256M); flat over time = no leak |

### C. Is it delivering to the firewalls?

| # | Check | Command | Healthy |
|---|-------|---------|---------|
| C1 | DLQ depth **and rate** | `redis-cli LLEN userid_dlq` + per-day breakdown (DLQ section above) | Rate ≤ ~0.3/day (summer 2026 baseline). Compare rate, not just depth |
| C2 | DLQ cause | `sudo grep -h "Permanent auth\|Max retries\|Connection error\|Commit-window" $LOGS \| sed -E 's/.*app\.paloalto: //; s/[0-9]+/N/g' \| sort \| uniq -c \| sort -rn` | Few lines; note which cause dominates (403 vs timeout) and which firewall |
| C3 | Firewall split | DLQ `targets` field | Single-firewall failures point at that box/its path; simultaneous failures point at a shared cause (commit, network) |

### D. Will it keep working?

| # | Check | Command | Healthy |
|---|-------|---------|---------|
| D1 | PA cert expiry | `sudo /opt/mist-userid/scripts/check_pa_certs.sh 30` | `OK` on both, >30 days. **Renew ~30 days before expiry**; timer warnings only land in logs |
| D2 | Cert-check timer ran | `systemctl list-timers mist-userid-certcheck.timer` | LAST within 24h |
| D3 | Time sync | `timedatectl \| grep synchronized` | `yes` (stale-event detection depends on it) |
| D4 | Disk | `df -h / /var /var/log` | <80%. **`/var/log` is a separate 5G volume** and the tightest: rsyslog `messages*` (~650MB/week × ~5) + journald (≤1G) |
| D5 | Dependency CVEs | `python3.12 -m pip_audit -r requirements.txt` (dev box) | No findings; else upgrade prod venv + restart |
| D6 | CI | `gh run list --repo WinSe7en/mist-userid --limit 1` | `success` |
| D7 | Redis persistence | `redis-cli info persistence \| grep -E "rdb_last_bgsave_status\|rdb_last_save_time"` (convert with `date -d @<time>`) | `ok`, last save within the last hour. A failing save means the queue/DLQ won't survive a restart; usually a full or unwritable `/var/lib/redis` |
| D8 | Redis memory & exposure | `redis-cli info memory \| grep used_memory_human`; `redis-cli config get bind` | A few MB (Nov 2026: ~1MB). Bound to `127.0.0.1 -::1` only. It has no password, which is safe only while it stays localhost-bound; add auth before any move to a shared Redis |

### E. Housekeeping after the review
- Record findings in the Known Issues table below if anything new turned up
- Archive then clear the DLQ for a fresh baseline: `redis-cli --no-raw LRANGE userid_dlq 0 -1 > ~/dlq-archive-$(date +%F).txt && redis-cli DEL userid_dlq`

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
2. While cert is being renewed, the unaffected target handles all traffic (worker falls back automatically)
3. **If renewal will take more than a few hours** and mappings to the affected target matter, use the break-glass option — set in `/etc/mist-userid/env`:
   ```
   PA_VERIFY_SSL=false
   ```
   then `sudo systemctl restart mist-userid-worker mist-userid-api`. The worker logs a WARNING at every startup while this is set. **Revert to `true` and restart immediately after the cert is renewed** — the PA API key travels over the connection this protects.
4. After cert renewed (and `PA_VERIFY_SSL` reverted), restart the worker to clear the SSL error state:
   ```bash
   sudo systemctl restart mist-userid-worker
   ```
5. Verify both targets responding:
   ```bash
   sudo journalctl -u mist-userid-worker -f 2>/dev/null | grep "HTTP Request"
   ```
6. Clear DLQ accumulated during outage: `redis-cli DEL userid_dlq`

**Prevention:**
- **Automated daily check**: `mist-userid-certcheck.timer` runs `/opt/mist-userid/scripts/check_pa_certs.sh` every morning at ~08:00; certs within 30 days of expiry log WARNING to journald (forwarded to StellarCyber via rsyslog) and fail the unit (visible in `systemctl --failed` and Zabbix).
   ```bash
   # Manual check anytime:
   sudo /opt/mist-userid/scripts/check_pa_certs.sh
   ```
- Zabbix item available: `mist.userid.cert.days[<host>]` (suggested triggers: `<30` warning, `<7` high)
- Both PA certs currently expire **Dec 12, 2026** — calendar reminder set for ~Nov 12, 2026; the daily check is the backstop

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
| Sep 3 – Nov 24, 2026 | DLQ rate rose ~5× (120 batches, 302 logins lost) from 403 "User not authorized to perform this operation." on both firewalls, dead-lettered as permanent | Classified as transient (retried like commit-window 403) on Nov 24 |
| Nov 24, 2026 | journald RAM-only, held ~8 hrs; monthly reviews couldn't see history | Made persistent, capped 1G (`deploy/journald-mist-userid.conf`) |
