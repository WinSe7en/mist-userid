import logging
from urllib.parse import quote

from app.config import get_settings
from app.redis_client import get_redis
from app.utils import sanitize_username

logger = logging.getLogger(__name__)


def _make_dedup_key(username: str, ip: str) -> str:
    """Build a collision-safe Redis key for deduplication.

    URL-encodes the username to prevent collisions from usernames
    containing ':' or other special characters.
    """
    return f"dedup:{quote(username, safe='')}:{ip}"


async def is_duplicate(username: str, ip: str) -> bool:
    r = await get_redis()
    settings = get_settings()
    key = _make_dedup_key(username, ip)
    was_set = await r.set(key, "1", nx=True, ex=settings.dedup_ttl)
    # SET NX returns True if key was newly set (not a duplicate)
    # Returns None if key already existed (is a duplicate)
    duplicate = was_set is None
    if duplicate:
        logger.debug("Dedup HIT: user=%s ip=%s (TTL %ds)", sanitize_username(username), ip, settings.dedup_ttl)
    else:
        logger.debug("Dedup MISS: user=%s ip=%s (TTL %ds)", sanitize_username(username), ip, settings.dedup_ttl)
    return duplicate
