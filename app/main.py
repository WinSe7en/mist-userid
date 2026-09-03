import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__
from app.config import get_settings
from app.logging_config import configure_logging
from app.pa_auth import get_api_key
from app.redis_client import close_redis, get_redis
from app.webhook import router as webhook_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
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
    settings = get_settings()
    errors = []
    targets_status = {}
    api_key_status = "unknown"

    try:
        r = await get_redis()
        await r.ping()
    except Exception as e:
        errors.append(f"redis: {e}")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=3.0),
        verify=settings.pa_verify_ssl,
    ) as client:
        # Verify API key is obtainable (static or generatable)
        try:
            await get_api_key(client)
            api_key_status = "valid"
        except Exception as e:
            api_key_status = f"error: {e}"
            errors.append(f"api_key: {e}")

        for target in settings.pa_target_list:
            try:
                await client.head(target, follow_redirects=False)
                targets_status[target] = "reachable"
            except Exception as e:
                targets_status[target] = f"unreachable: {type(e).__name__}"
                errors.append(f"pa:{target}: {type(e).__name__}")

    if errors:
        return {"status": "not ready", "errors": errors, "targets": targets_status, "api_key": api_key_status}
    return {"status": "ready", "targets": targets_status, "api_key": api_key_status}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
