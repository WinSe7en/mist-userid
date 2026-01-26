import asyncio
import json
import logging
import time
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx

from app.config import get_settings
from app.metrics import DLQ_EVENTS, PA_REQUEST_DURATION, PA_REQUESTS
from app.pa_auth import get_api_key, invalidate_api_key
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
DLQ_KEY = "userid_dlq"


def build_uid_xml(
    logins: list[tuple[str, str]],
    logouts: list[tuple[str, str]],
    timeout: int,
) -> str:
    uid_message = Element("uid-message")
    SubElement(uid_message, "type").text = "update"
    payload = SubElement(uid_message, "payload")

    if logins:
        login_el = SubElement(payload, "login")
        for username, ip in logins:
            SubElement(login_el, "entry", name=username, ip=ip, timeout=str(timeout))

    if logouts:
        logout_el = SubElement(payload, "logout")
        for username, ip in logouts:
            SubElement(logout_el, "entry", name=username, ip=ip)

    return tostring(uid_message, encoding="unicode")


async def send_to_target(
    client: httpx.AsyncClient,
    target: str,
    xml_body: str,
    api_key: str,
    max_retries: int,
    allow_key_refresh: bool = True,
) -> bool:
    url = f"{target.rstrip('/')}/api/"
    data = {
        "type": "user-id",
        "key": api_key,
        "cmd": xml_body,
    }

    logger.debug("Sending to target %s (max_retries=%d)", target, max_retries)

    for attempt in range(max_retries + 1):
        try:
            start = time.monotonic()
            resp = await client.post(url, data=data)
            duration = time.monotonic() - start
            PA_REQUEST_DURATION.labels(target=target).observe(duration)

            if resp.status_code == 200:
                # Check for success or benign logout failures
                if "success" in resp.text:
                    logger.debug("Success from %s (attempt %d): %s",
                                 target, attempt + 1, resp.text[:100])
                    PA_REQUESTS.labels(target=target, status="success").inc()
                    return True

                # "Delete mapping failed" means logout for non-existent user — benign
                # This happens when the mapping already timed out or was never created
                if "Delete mapping failed" in resp.text and "status=\"error\"" in resp.text:
                    logger.debug(
                        "Benign logout failure from %s (user not in table): %s",
                        target, resp.text[:200],
                    )
                    PA_REQUESTS.labels(target=target, status="success").inc()
                    return True

            if resp.status_code == 401:
                if allow_key_refresh:
                    logger.warning(
                        "Auth failure (401) from %s, attempting key refresh", target
                    )
                    invalidate_api_key()
                    try:
                        new_key = await get_api_key(client)
                        logger.info("Regenerated API key, retrying request to %s", target)
                        return await send_to_target(
                            client, target, xml_body, new_key, max_retries,
                            allow_key_refresh=False,
                        )
                    except Exception as e:
                        logger.error("Failed to regenerate API key: %s", e)
                        PA_REQUESTS.labels(target=target, status="failure").inc()
                        return False
                else:
                    logger.error(
                        "Permanent auth failure from %s: 401 (already refreshed key)",
                        target,
                    )
                    PA_REQUESTS.labels(target=target, status="failure").inc()
                    return False

            if resp.status_code == 403:
                logger.error(
                    "Permanent auth failure from %s: %d", target, resp.status_code
                )
                PA_REQUESTS.labels(target=target, status="failure").inc()
                return False

            if resp.status_code in TRANSIENT_STATUS_CODES:
                if attempt < max_retries:
                    delay = 2**attempt
                    logger.warning(
                        "Transient error %d from %s, retry %d/%d in %ds",
                        resp.status_code, target, attempt + 1, max_retries, delay,
                    )
                    PA_REQUESTS.labels(target=target, status="retry").inc()
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "Max retries reached for %s (last status: %d)",
                    target, resp.status_code,
                )
                PA_REQUESTS.labels(target=target, status="failure").inc()
                return False

            # Unexpected status
            logger.error("Unexpected response from %s: %d %s",
                         target, resp.status_code, resp.text[:200])
            PA_REQUESTS.labels(target=target, status="failure").inc()
            return False

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < max_retries:
                delay = 2**attempt
                logger.warning(
                    "Connection error to %s: %s, retry %d/%d in %ds",
                    target, type(e).__name__, attempt + 1, max_retries, delay,
                )
                PA_REQUESTS.labels(target=target, status="retry").inc()
                await asyncio.sleep(delay)
                continue
            logger.error("Max retries reached for %s: %s", target, e)
            PA_REQUESTS.labels(target=target, status="failure").inc()
            return False

    return False


async def send_batch(
    client: httpx.AsyncClient,
    logins: list[tuple[str, str]],
    logouts: list[tuple[str, str]],
) -> list[str]:
    """Send batch to all PA targets. Returns list of failed target URLs."""
    if not logins and not logouts:
        return []

    settings = get_settings()
    xml_body = build_uid_xml(logins, logouts, settings.userid_timeout)

    logger.info(
        "Sending batch to %d targets: %d logins, %d logouts",
        len(settings.pa_target_list), len(logins), len(logouts),
    )
    logger.debug("XML payload (%d bytes): %s", len(xml_body), xml_body)

    # Get API key once for all targets
    api_key = await get_api_key(client)

    results = await asyncio.gather(
        *[
            send_to_target(
                client, target, xml_body,
                api_key, settings.max_retry_attempts,
            )
            for target in settings.pa_target_list
        ],
        return_exceptions=True,
    )

    failed_targets = []
    for target, result in zip(settings.pa_target_list, results):
        if isinstance(result, Exception):
            logger.error("Unexpected exception sending to %s: %s", target, result)
            PA_REQUESTS.labels(target=target, status="failure").inc()
            failed_targets.append(target)
        elif not result:
            failed_targets.append(target)

    if failed_targets:
        await _dead_letter(failed_targets, logins, logouts)

    return failed_targets


async def _dead_letter(
    failed_targets: list[str],
    logins: list[tuple[str, str]],
    logouts: list[tuple[str, str]],
) -> None:
    """Push failed batch entries to the dead-letter queue."""
    import time as _time

    entry = json.dumps({
        "timestamp": _time.time(),
        "targets": failed_targets,
        "logins": logins,
        "logouts": logouts,
        "error": f"All retries exhausted for targets: {', '.join(failed_targets)}",
    })

    try:
        r = await get_redis()
        await r.lpush(DLQ_KEY, entry)
        DLQ_EVENTS.inc()
        logger.warning(
            "Dead-lettered batch (%d logins, %d logouts) for targets: %s",
            len(logins), len(logouts), ", ".join(failed_targets),
        )
    except Exception as e:
        logger.error("Failed to write to DLQ: %s", e)
