from app.config import get_settings
from app.redis_client import get_redis


async def is_duplicate(username: str, ip: str) -> bool:
    r = await get_redis()
    settings = get_settings()
    key = f"dedup:{username}:{ip}"
    was_set = await r.set(key, "1", nx=True, ex=settings.dedup_ttl)
    # SET NX returns True if key was newly set (not a duplicate)
    # Returns None if key already existed (is a duplicate)
    return was_set is None
