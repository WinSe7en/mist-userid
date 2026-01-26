"""Palo Alto API key generation and caching."""

import logging
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_cached_api_key: Optional[str] = None


async def generate_api_key(
    client: httpx.AsyncClient,
    target: str,
    username: str,
    password: str,
) -> str:
    """Generate API key via PA keygen endpoint.

    Args:
        client: HTTP client for making requests
        target: PA firewall URL (e.g., https://pan03.example.com)
        username: Admin username
        password: Admin password

    Returns:
        Generated API key string

    Raises:
        ValueError: If keygen fails or response is invalid
        httpx.HTTPStatusError: If HTTP request fails
    """
    url = f"{target.rstrip('/')}/api/"
    data = {
        "type": "keygen",
        "user": username,
        "password": password,
    }

    resp = await client.post(url, data=data)

    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"HTTP {resp.status_code}: {resp.text[:100]}",
            request=None,
            response=resp,
        )

    # Parse XML response:
    # <response status="success">
    #   <result>
    #     <key>LUFRPT14MW5xOEo1R09...</key>
    #   </result>
    # </response>
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML response from {target}: {e}") from e

    status = root.get("status")
    if status != "success":
        msg_el = root.find(".//msg")
        msg = msg_el.text if msg_el is not None else "Unknown error"
        raise ValueError(f"Keygen failed on {target}: {msg}")

    key_el = root.find(".//result/key")
    if key_el is None or not key_el.text:
        raise ValueError(f"No API key in keygen response from {target}")

    return key_el.text


async def get_api_key(client: httpx.AsyncClient) -> str:
    """Get cached API key, or generate if needed.

    If PA_API_KEY is configured, returns it directly.
    Otherwise, generates a key from the first reachable PA target.

    Args:
        client: HTTP client for making requests

    Returns:
        API key string

    Raises:
        RuntimeError: If no API key could be obtained from any target
    """
    global _cached_api_key
    settings = get_settings()

    # If static key configured, use it (backward compatible)
    if settings.pa_api_key:
        return settings.pa_api_key

    # Return cached key if available
    if _cached_api_key:
        return _cached_api_key

    # Generate from first reachable target
    errors = []
    for target in settings.pa_target_list:
        try:
            _cached_api_key = await generate_api_key(
                client, target, settings.pa_username, settings.pa_password
            )
            logger.info("Generated API key from %s", target)
            return _cached_api_key
        except Exception as e:
            logger.warning("Failed to generate key from %s: %s", target, e)
            errors.append(f"{target}: {e}")

    raise RuntimeError(
        f"Could not generate API key from any target: {'; '.join(errors)}"
    )


def invalidate_api_key() -> None:
    """Clear cached key (call on 401 to force regeneration)."""
    global _cached_api_key
    if _cached_api_key:
        logger.info("Invalidating cached API key")
        _cached_api_key = None


def get_cached_key() -> Optional[str]:
    """Return the currently cached key (for testing)."""
    return _cached_api_key
