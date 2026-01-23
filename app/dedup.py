import logging

from app.config import get_settings
from app.redis_client import get_redis

logger = logging.getLogger(__name__)


async def is_duplicate(username: str, ip: str) -> bool:
    r = await get_redis()
    settings = get_settings()
    key = f"dedup:{username}:{ip}"
    was_set = await r.set(key, "1", nx=True, ex=settings.dedup_ttl)
    # SET NX returns True if key was newly set (not a duplicate)
    # Returns None if key already existed (is a duplicate)
    duplicate = was_set is None
    if duplicate:
        logger.debug("Dedup HIT: %s (TTL %ds remaining)", key, settings.dedup_ttl)
    else:
        logger.debug("Dedup MISS (new): %s (TTL %ds)", key, settings.dedup_ttl)
    return duplicate
