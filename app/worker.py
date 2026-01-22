import asyncio
import json
import logging
import signal
import time

import httpx
import sdnotify

from app.config import get_settings
from app.dedup import is_duplicate
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

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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
                    continue

                key = (username, ip)

                if await is_duplicate(username, ip):
                    continue

                action = classify_event(event)
                if action == "login":
                    batch_logins[key] = (username, ip)
                    batch_logouts.pop(key, None)
                else:
                    if key not in batch_logins:
                        batch_logouts[key] = (username, ip)

            total = len(batch_logins) + len(batch_logouts)
            elapsed = time.monotonic() - last_flush
            should_flush = (
                total >= settings.batch_size
                or (total > 0 and elapsed >= settings.batch_flush_interval)
            )

            if should_flush:
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
            logger.info("Flushing remaining batch before shutdown...")
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
