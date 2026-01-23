import asyncio
import logging
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


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
            resp = await client.post(url, data=data)

            if resp.status_code == 200 and "success" in resp.text:
                logger.debug("Success from %s (attempt %d): %s",
                             target, attempt + 1, resp.text[:100])
                return True

            if resp.status_code in {401, 403}:
                logger.error(
                    "Permanent auth failure from %s: %d", target, resp.status_code
                )
                return False

            if resp.status_code in TRANSIENT_STATUS_CODES:
                if attempt < max_retries:
                    delay = 2**attempt
                    logger.warning(
                        "Transient error %d from %s, retry %d/%d in %ds",
                        resp.status_code, target, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "Max retries reached for %s (last status: %d)",
                    target, resp.status_code,
                )
                return False

            # Unexpected status
            logger.error("Unexpected response from %s: %d %s",
                         target, resp.status_code, resp.text[:200])
            return False

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < max_retries:
                delay = 2**attempt
                logger.warning(
                    "Connection error to %s: %s, retry %d/%d in %ds",
                    target, type(e).__name__, attempt + 1, max_retries, delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.error("Max retries reached for %s: %s", target, e)
            return False

    return False


async def send_batch(
    client: httpx.AsyncClient,
    logins: list[tuple[str, str]],
    logouts: list[tuple[str, str]],
) -> None:
    if not logins and not logouts:
        return

    settings = get_settings()
    xml_body = build_uid_xml(logins, logouts, settings.userid_timeout)

    logger.info(
        "Sending batch to %d targets: %d logins, %d logouts",
        len(settings.pa_target_list), len(logins), len(logouts),
    )
    logger.debug("XML payload (%d bytes): %s", len(xml_body), xml_body)

    results = await asyncio.gather(
        *[
            send_to_target(
                client, target, xml_body,
                settings.pa_api_key, settings.max_retry_attempts,
            )
            for target in settings.pa_target_list
        ],
        return_exceptions=True,
    )

    for target, result in zip(settings.pa_target_list, results):
        if isinstance(result, Exception):
            logger.error("Unexpected exception sending to %s: %s", target, result)
        elif not result:
            logger.error("Failed to send batch to %s", target)
