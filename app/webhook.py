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

    logger.debug("Received webhook: %d bytes", len(body))

    signature = request.headers.get("X-Mist-Signature-v2")
    if not verify_signature(settings.mist_webhook_secret, body, signature):
        logger.warning("Webhook signature validation failed")
        return Response(status_code=401)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.debug("Failed to parse webhook body as JSON")
        return Response(status_code=400)

    topic = payload.get("topic")
    if topic not in VALID_TOPICS:
        logger.debug("Ignoring unhandled topic: %s", topic)
        return Response(status_code=202)

    events = payload.get("events", [])
    logger.debug("Processing topic=%s with %d events", topic, len(events))

    r = await get_redis()
    queued = 0

    for event in events:
        username = extract_username(event)
        ip = event.get("client_ip")

        if not username:
            logger.debug("Skipping event: no username (mac=%s)", event.get("mac"))
            continue
        if not ip:
            logger.debug("Skipping event: no IP for user=%s", username)
            continue
        if not is_valid_ip(ip):
            logger.debug("Skipping event: invalid IP=%s for user=%s", ip, username)
            continue

        event["_topic"] = topic
        await r.lpush(QUEUE_KEY, json.dumps(event))
        queued += 1
        logger.debug("Queued: user=%s ip=%s topic=%s", username, ip, topic)

    logger.debug("Queued %d/%d events from %s", queued, len(events), topic)
    return Response(
        content=json.dumps({"status": "accepted", "queued": queued}),
        status_code=202,
        media_type="application/json",
    )
