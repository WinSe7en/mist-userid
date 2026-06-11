import asyncio
import json
import logging
import signal
import time

import httpx
import sdnotify
from redis.exceptions import RedisError

from app.config import get_settings
from app.dedup import is_duplicate
from app.logging_config import configure_logging
from app.metrics import (
    BATCH_FLUSHES,
    BATCH_SIZE,
    EVENTS_DEDUPED,
    EVENTS_INVALID_USERNAME,
    EVENTS_PROCESSED,
    QUEUE_DEPTH,
)
from app.pa_auth import get_api_key
from app.paloalto import send_batch
from app.redis_client import close_redis, get_redis
from app.utils import sanitize_username
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


# Maximum username length for PA XML (conservative limit)
MAX_USERNAME_LENGTH = 255

# Watchdog heartbeat interval; must be well under WatchdogSec=30 in the unit file
WATCHDOG_INTERVAL = 10.0


async def watchdog_heartbeat() -> None:
    """Feed the systemd watchdog from a dedicated task.

    A batch flush can legitimately exceed WatchdogSec (PA retry backoff
    totals 31s of sleep, more with connect timeouts), so the main loop
    cannot be the heartbeat source. This task proves the event loop is
    alive; a genuinely frozen loop stops it too, so real hangs still
    trip the watchdog.
    """
    while True:
        notifier.notify("WATCHDOG=1")
        await asyncio.sleep(WATCHDOG_INTERVAL)


def validate_username(username) -> tuple[bool, str]:
    """Validate username before XML construction.

    Returns:
        (is_valid, reason) - reason is empty string if valid
    """
    if not isinstance(username, str):
        return False, "not_a_string"
    if len(username) > MAX_USERNAME_LENGTH:
        return False, "too_long"
    # Reject control characters (ASCII 0-31, includes null bytes)
    for char in username:
        if ord(char) < 32:
            return False, "control_chars"
    return True, ""


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
        # Pre-generate/validate API key at startup (fail fast if credentials invalid)
        try:
            await get_api_key(client)
            logger.info("API key validated/generated successfully")
        except Exception as e:
            logger.error("Failed to obtain API key at startup: %s", e)
            raise

        batch_logins: dict[tuple[str, str], tuple[str, str]] = {}
        batch_logouts: dict[tuple[str, str], tuple[str, str]] = {}
        last_flush = time.monotonic()

        notifier.notify("READY=1")
        heartbeat_task = asyncio.create_task(watchdog_heartbeat())

        try:
            while not shutdown_event.is_set():
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
                except RedisError as e:
                    # Redis down/restarting — back off instead of crashing,
                    # which would dump the in-memory batch
                    logger.warning("Redis error on brpop: %s; retrying in 5s", e)
                    await asyncio.sleep(5)
                    continue

                if result:
                    _, raw = result
                    try:
                        event = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Invalid event in queue, skipping")
                        continue

                    try:
                        username = event.get("client_username") or event.get("psk_name")
                        ip = event.get("client_ip")
                        if not username or not isinstance(ip, str) or not ip:
                            logger.debug("Skipping event from queue: missing username or IP")
                            continue

                        # Validate username before XML construction
                        is_valid, reason = validate_username(username)
                        if not is_valid:
                            logger.warning(
                                "Skipping event: invalid username (reason=%s, type=%s, len=%d)",
                                reason, type(username).__name__, len(str(username)),
                            )
                            EVENTS_INVALID_USERNAME.labels(reason=reason).inc()
                            continue

                        key = (username, ip)

                        if await is_duplicate(username, ip):
                            logger.debug("Dedup skip: user=%s ip=%s", sanitize_username(username), ip)
                            EVENTS_DEDUPED.inc()
                            continue

                        action = classify_event(event)
                        logger.debug(
                            "Event: user=%s ip=%s action=%s topic=%s next_ap=%s",
                            sanitize_username(username), ip, action,
                            event.get("_topic"), event.get("next_ap", "N/A"),
                        )
                        EVENTS_PROCESSED.labels(action=action).inc()

                        if action == "login":
                            batch_logins[key] = (username, ip)
                            if key in batch_logouts:
                                logger.debug("Login supersedes logout: user=%s ip=%s", sanitize_username(username), ip)
                                batch_logouts.pop(key, None)
                        else:
                            if key not in batch_logins:
                                batch_logouts[key] = (username, ip)
                            else:
                                logger.debug(
                                    "Logout ignored (login already in batch): user=%s ip=%s",
                                    sanitize_username(username), ip,
                                )
                    except RedisError as e:
                        # Dedup check failed — Redis blipped after brpop succeeded.
                        # Skip this event rather than crash; it was already consumed.
                        logger.warning("Redis error during event processing: %s; event skipped", e)
                        continue
                    except Exception:
                        # A single malformed event must never kill the worker
                        # (and with it the in-memory batch)
                        logger.exception("Unexpected error processing event, skipping")
                        continue

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
        finally:
            heartbeat_task.cancel()

    await close_redis()
    notifier.notify("STOPPING=1")
    logger.info("Worker stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
