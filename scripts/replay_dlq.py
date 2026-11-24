"""Replay recent dead-lettered batches to the PA firewalls.

Uses the service's own sender (app.paloalto.send_batch), so replays get the
configured USERID_TIMEOUT, retries, transient-403 handling and key refresh, and
a batch that fails again is dead-lettered again rather than lost.

Only entries younger than --max-age are replayed: older user-to-IP mappings may
be wrong after DHCP reassignment. Older entries are left in the DLQ untouched.

Usage (production):
    cd /opt/mist-userid
    sudo venv/bin/python scripts/replay_dlq.py --dry-run
    sudo venv/bin/python scripts/replay_dlq.py --max-age 300
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.paloalto import DLQ_KEY, send_batch  # noqa: E402
from app.redis_client import close_redis, get_redis  # noqa: E402


def select_entries(raw_entries: list[str], max_age: float, now: float) -> tuple[list, int]:
    """Return ([(raw, entry), ...] to replay, count of stale/unreadable skipped)."""
    fresh, skipped = [], 0
    for raw in raw_entries:
        try:
            entry = json.loads(raw)
            age = now - float(entry["timestamp"])
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        if age <= max_age:
            fresh.append((raw, entry))
        else:
            skipped += 1
    return fresh, skipped


async def replay(max_age: float, dry_run: bool) -> int:
    settings = get_settings()
    configure_logging(settings)
    r = await get_redis()

    raw_entries = await r.lrange(DLQ_KEY, 0, -1)
    fresh, skipped = select_entries(raw_entries, max_age, time.time())
    print(f"DLQ: {len(raw_entries)} entries, {len(fresh)} within {max_age:.0f}s, "
          f"{skipped} older (left in place)")

    if dry_run or not fresh:
        await close_redis()
        return 0

    failed_batches = 0
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        verify=settings.pa_verify_ssl,
    ) as client:
        for raw, entry in fresh:
            # Remove first: send_batch re-dead-letters the batch if it fails again
            await r.lrem(DLQ_KEY, 1, raw)
            logins = [tuple(x) for x in entry.get("logins", [])]
            logouts = [tuple(x) for x in entry.get("logouts", [])]
            failed = await send_batch(client, logins, logouts)
            if failed:
                failed_batches += 1
    print(f"Replayed {len(fresh)} batches: {len(fresh) - failed_batches} ok, "
          f"{failed_batches} failed again (back in the DLQ)")
    await close_redis()
    return 1 if failed_batches else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-age", type=float, default=300,
                        help="replay only entries younger than this many seconds (default 300)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be replayed without sending")
    args = parser.parse_args()
    sys.exit(asyncio.run(replay(args.max_age, args.dry_run)))


if __name__ == "__main__":
    main()
