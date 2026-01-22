import hashlib
import hmac
import ipaddress
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, Response

from app.config import get_settings
from app.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_TOPICS = {"client-sessions", "client-join"}
QUEUE_KEY = "userid_queue"


def verify_signature(secret: str, body: bytes, signature: Optional[str]) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_valid_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_link_local or addr == ipaddress.ip_address("0.0.0.0"):
        return False
    return True


def extract_username(event: dict) -> Optional[str]:
    return event.get("client_username") or event.get("psk_name") or None


@router.post("/mist/webhook")
async def receive_webhook(request: Request) -> Response:
    settings = get_settings()
    body = await request.body()

    signature = request.headers.get("X-Mist-Signature-v2")
    if not verify_signature(settings.mist_webhook_secret, body, signature):
        logger.warning("Webhook signature validation failed")
        return Response(status_code=401)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(status_code=400)

    topic = payload.get("topic")
    if topic not in VALID_TOPICS:
        return Response(status_code=202)

    r = await get_redis()
    queued = 0

    for event in payload.get("events", []):
        username = extract_username(event)
        ip = event.get("client_ip")

        if not username or not ip:
            continue
        if not is_valid_ip(ip):
            continue

        event["_topic"] = topic
        await r.lpush(QUEUE_KEY, json.dumps(event))
        queued += 1

    logger.debug("Queued %d events from %s", queued, topic)
    return Response(
        content=json.dumps({"status": "accepted", "queued": queued}),
        status_code=202,
        media_type="application/json",
    )
