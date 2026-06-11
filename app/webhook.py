import hashlib
import hmac
import ipaddress
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Request, Response

from app.config import get_settings
from app.metrics import EVENTS_QUEUED, EVENTS_RECEIVED, EVENTS_REJECTED, WEBHOOK_QUEUE_FULL
from app.redis_client import get_redis
from app.utils import sanitize_username

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_TOPICS = {"client-sessions", "client-join"}
QUEUE_KEY = "userid_queue"


def verify_signature(secret: str, body: bytes, signature: Optional[str]) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_valid_ip(ip) -> bool:
    # ipaddress.ip_address() accepts integers, so require a string explicitly
    if not isinstance(ip, str):
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_link_local or addr.is_unspecified or addr.is_loopback or addr.is_multicast:
        return False
    return True


def is_fresh_event(event: dict, max_age: int) -> bool:
    """Check if event timestamp is within max_age seconds of current time."""
    ts = event.get("timestamp")
    if ts is None:
        return True  # No timestamp → allow (don't break on unexpected payloads)
    try:
        return abs(time.time() - float(ts)) <= max_age
    except (TypeError, ValueError):
        return True  # Non-numeric timestamp → allow


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
    if not isinstance(events, list):
        logger.debug("Invalid events field: not a list")
        return Response(status_code=400)

    r = await get_redis()

    # Queue depth cap — reject entire webhook if queue is full
    queue_depth = await r.llen(QUEUE_KEY)
    if queue_depth >= settings.max_queue_depth:
        logger.warning("Queue full (%d >= %d), rejecting webhook",
                        queue_depth, settings.max_queue_depth)
        WEBHOOK_QUEUE_FULL.inc()
        return Response(
            content=json.dumps({"error": "queue full"}),
            status_code=429,
            media_type="application/json",
        )

    logger.debug("Processing topic=%s with %d events", topic, len(events))
    EVENTS_RECEIVED.labels(topic=topic).inc(len(events))

    queued = 0

    for event in events:
        if not isinstance(event, dict):
            logger.debug("Skipping malformed event: not an object (type=%s)",
                         type(event).__name__)
            EVENTS_REJECTED.labels(reason="malformed_event").inc()
            continue

        username = extract_username(event)
        ip = event.get("client_ip")

        if not is_fresh_event(event, settings.webhook_max_age):
            age = int(time.time() - float(event.get("timestamp", 0)))
            logger.debug(
                "Skipping stale event: age=%ds site=%s ap=%s ssid=%s",
                age,
                event.get("site_name", "unknown"),
                event.get("ap_name", "unknown"),
                event.get("ssid", "unknown"),
            )
            EVENTS_REJECTED.labels(reason="stale_event").inc()
            continue

        ssid = event.get("ssid", "")
        if isinstance(ssid, str) and ssid and ssid.lower() in settings.ignore_ssid_set:
            logger.debug("Skipping event: ignored SSID=%s", ssid)
            EVENTS_REJECTED.labels(reason="ignored_ssid").inc()
            continue
        if not username:
            logger.debug("Skipping event: no username (mac=%s)", event.get("mac"))
            EVENTS_REJECTED.labels(reason="no_username").inc()
            continue
        if not isinstance(username, str):
            # sanitize_username() below requires a string; reject odd types here
            logger.debug("Skipping event: non-string username (type=%s)",
                         type(username).__name__)
            EVENTS_REJECTED.labels(reason="invalid_username").inc()
            continue
        if not ip:
            logger.debug("Skipping event: no IP for user=%s", sanitize_username(username))
            EVENTS_REJECTED.labels(reason="no_ip").inc()
            continue
        if not is_valid_ip(ip):
            logger.debug("Skipping event: invalid IP=%s for user=%s", ip, sanitize_username(username))
            EVENTS_REJECTED.labels(reason="invalid_ip").inc()
            continue

        event["_topic"] = topic
        await r.lpush(QUEUE_KEY, json.dumps(event))
        queued += 1
        EVENTS_QUEUED.inc()
        logger.debug("Queued: user=%s ip=%s topic=%s", sanitize_username(username), ip, topic)

    logger.debug("Queued %d/%d events from %s", queued, len(events), topic)
    return Response(
        content=json.dumps({"status": "accepted", "queued": queued}),
        status_code=202,
        media_type="application/json",
    )
