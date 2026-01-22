import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.redis_client import close_redis, get_redis
from app.webhook import router as webhook_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Mist User-ID API v%s starting", __version__)
    await get_redis()
    yield
    await close_redis()
    logger.info("Mist User-ID API stopped")


app = FastAPI(
    title="Mist User-ID",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/ready")
async def ready():
    errors = []

    try:
        r = await get_redis()
        await r.ping()
    except Exception as e:
        errors.append(f"redis: {e}")

    if errors:
        return {"status": "not ready", "errors": errors}
    return {"status": "ready"}
