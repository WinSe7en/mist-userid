import asyncio
import json
import logging
import signal
import time

import httpx
import sdnotify

from app.config import get_settings
from app.dedup import is_duplicate
from app.logging_config import configure_logging
from app.metrics import (
    BATCH_FLUSHES,
    BATCH_SIZE,
    EVENTS_DEDUPED,
    EVENTS_PROCESSED,
    QUEUE_DEPTH,
)
from app.paloalto import send_batch
from app.redis_client import close_redis, get_redis
from app.webhook import QUEUE_KEY

logger = logging.getLogger(__name__)
notifier = sdnotify.SystemdNotifier()


def classify_event(event: dict) -> str:
    topic = event.get("_topic", "")
    if topic == "client-join":
        return "login"
    next_ap = event.get("next_ap", "000000000000")
    if next_ap != "000000000000":
        return "login"
    return "logout"


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings)

    logger.info("Worker starting (batch_size=%d, flush_interval=%.1fs)",
                settings.batch_size, settings.batch_flush_interval)

    shutdown_event = asyncio.Event()

    def handle_signal(sig: int, _frame) -> None:
        logger.info("Received signal %d, shutting down...", sig)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    r = await get_redis()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        verify=True,
    ) as client:
        batch_logins: dict[tuple[str, str], tuple[str, str]] = {}
        batch_logouts: dict[tuple[str, str], tuple[str, str]] = {}
        last_flush = time.monotonic()

        notifier.notify("READY=1")

        while not shutdown_event.is_set():
            notifier.notify("WATCHDOG=1")

            # Update queue depth gauge
            try:
                depth = await r.llen(QUEUE_KEY)
                QUEUE_DEPTH.set(depth)
            except Exception:
                pass

            try:
                result = await asyncio.wait_for(
                    r.brpop(QUEUE_KEY, timeout=1), timeout=2.0
                )
            except (asyncio.TimeoutError, TimeoutError):
                result = None

            if result:
                _, raw = result
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Invalid event in queue, skipping")
                    continue

                username = event.get("client_username") or event.get("psk_name")
                ip = event.get("client_ip")
                if not username or not ip:
                    logger.debug("Skipping event from queue: missing username or IP")
                    continue

                key = (username, ip)

                if await is_duplicate(username, ip):
                    logger.debug("Dedup skip: user=%s ip=%s", username, ip)
                    EVENTS_DEDUPED.inc()
                    continue

                action = classify_event(event)
                logger.debug(
                    "Event: user=%s ip=%s action=%s topic=%s next_ap=%s",
                    username, ip, action,
                    event.get("_topic"), event.get("next_ap", "N/A"),
                )
                EVENTS_PROCESSED.labels(action=action).inc()

                if action == "login":
                    batch_logins[key] = (username, ip)
                    if key in batch_logouts:
                        logger.debug("Login supersedes logout: user=%s ip=%s", username, ip)
                        batch_logouts.pop(key, None)
                else:
                    if key not in batch_logins:
                        batch_logouts[key] = (username, ip)
                    else:
                        logger.debug(
                            "Logout ignored (login already in batch): user=%s ip=%s",
                            username, ip,
                        )

            total = len(batch_logins) + len(batch_logouts)
            elapsed = time.monotonic() - last_flush
            should_flush = (
                total >= settings.batch_size
                or (total > 0 and elapsed >= settings.batch_flush_interval)
            )

            if should_flush:
                trigger = "size" if total >= settings.batch_size else "timer"
                logger.debug(
                    "Flushing batch: %d logins, %d logouts (trigger: %s)",
                    len(batch_logins), len(batch_logouts), trigger,
                )
                BATCH_FLUSHES.labels(trigger=trigger).inc()
                BATCH_SIZE.observe(total)
                await send_batch(
                    client,
                    list(batch_logins.values()),
                    list(batch_logouts.values()),
                )
                batch_logins = {}
                batch_logouts = {}
                last_flush = time.monotonic()

        # Graceful shutdown: flush remaining
        if batch_logins or batch_logouts:
            total = len(batch_logins) + len(batch_logouts)
            logger.info("Flushing remaining batch before shutdown...")
            BATCH_FLUSHES.labels(trigger="shutdown").inc()
            BATCH_SIZE.observe(total)
            await send_batch(
                client,
                list(batch_logins.values()),
                list(batch_logouts.values()),
            )

    await close_redis()
    notifier.notify("STOPPING=1")
    logger.info("Worker stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
