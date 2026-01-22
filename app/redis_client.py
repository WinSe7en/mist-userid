from typing import Optional

import redis.asyncio as redis

from app.config import get_settings

_pool: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
