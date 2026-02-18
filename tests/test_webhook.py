import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_with_redis(client, fake_redis):
    mock_resp = httpx.Response(200)
    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = await client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert "targets" in data


@pytest.mark.asyncio
async def test_ready_reports_unreachable_target(client, fake_redis):
    with patch("app.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = await client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not ready"
    assert any("pa:" in e for e in data["errors"])


@pytest.mark.asyncio
async def test_webhook_rejects_missing_signature(client, sample_client_join_event):
    body = json.dumps(sample_client_join_event).encode()
    resp = await client.post("/mist/webhook", content=body)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(client, sample_client_join_event):
    body = json.dumps(sample_client_join_event).encode()
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": "invalid-signature"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_accepts_valid_signature(
    client, fake_redis, sample_client_join_event, sign_payload
):
    body = json.dumps(sample_client_join_event).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["queued"] == 1


@pytest.mark.asyncio
async def test_webhook_queues_to_redis(
    client, fake_redis, sample_client_join_event, sign_payload
):
    body = json.dumps(sample_client_join_event).encode()
    sig = sign_payload(body)
    await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    queue_len = await fake_redis.llen("userid_queue")
    assert queue_len == 1

    raw = await fake_redis.rpop("userid_queue")
    event = json.loads(raw)
    assert event["client_username"] == "alex.taylor@example.edu"
    assert event["client_ip"] == "10.5.72.249"
    assert event["_topic"] == "client-join"


@pytest.mark.asyncio
async def test_webhook_ignores_unknown_topic(client, fake_redis, sign_payload):
    payload = {"topic": "location", "events": []}
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    queue_len = await fake_redis.llen("userid_queue")
    assert queue_len == 0


@pytest.mark.asyncio
async def test_webhook_skips_event_without_username(client, fake_redis, sign_payload):
    payload = {
        "topic": "client-join",
        "events": [
            {"ap": "test", "client_ip": "10.1.1.1", "mac": "aabbccddeeff"}
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 0


@pytest.mark.asyncio
async def test_webhook_skips_event_without_ip(client, fake_redis, sign_payload):
    payload = {
        "topic": "client-join",
        "events": [
            {"ap": "test", "client_username": "user@example.edu", "mac": "aabbccddeeff"}
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 0


@pytest.mark.asyncio
async def test_webhook_skips_link_local_ip(client, fake_redis, sign_payload):
    payload = {
        "topic": "client-join",
        "events": [
            {
                "client_username": "user@example.edu",
                "client_ip": "169.254.1.1",
                "mac": "aabbccddeeff",
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 0


@pytest.mark.asyncio
async def test_webhook_skips_zero_ip(client, fake_redis, sign_payload):
    payload = {
        "topic": "client-join",
        "events": [
            {
                "client_username": "user@example.edu",
                "client_ip": "0.0.0.0",
                "mac": "aabbccddeeff",
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 0


@pytest.mark.asyncio
async def test_webhook_uses_psk_name_fallback(
    client, fake_redis, sample_psk_event, sign_payload
):
    body = json.dumps(sample_psk_event).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 1

    raw = await fake_redis.rpop("userid_queue")
    event = json.loads(raw)
    assert event["psk_name"] == "Morgan.Riley@example.edu"


@pytest.mark.asyncio
async def test_webhook_handles_multiple_events(client, fake_redis, sign_payload):
    now = int(time.time())
    payload = {
        "topic": "client-sessions",
        "events": [
            {
                "client_username": "user1@example.edu",
                "client_ip": "10.1.1.1",
                "next_ap": "020000000a07",
                "termination_reason": 3,
                "timestamp": now,
            },
            {
                "client_username": "user2@example.edu",
                "client_ip": "10.1.1.2",
                "next_ap": "000000000000",
                "termination_reason": 1,
                "timestamp": now,
            },
            {
                "mac": "aabbccddeeff",  # no username, should be skipped
                "timestamp": now,
            },
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 2


# --- Security hardening tests ---


@pytest.mark.asyncio
async def test_webhook_rejects_stale_events(client, fake_redis, sign_payload):
    """Events with timestamps older than webhook_max_age should be rejected."""
    stale_ts = int(time.time()) - 600  # 10 minutes ago (max_age=300s)
    payload = {
        "topic": "client-join",
        "events": [
            {
                "client_username": "stale@example.edu",
                "client_ip": "10.1.1.1",
                "mac": "aabbccddeeff",
                "timestamp": stale_ts,
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 0


@pytest.mark.asyncio
async def test_webhook_accepts_recent_events(client, fake_redis, sign_payload):
    """Events with current timestamps should be accepted."""
    payload = {
        "topic": "client-join",
        "events": [
            {
                "client_username": "recent@example.edu",
                "client_ip": "10.1.1.1",
                "mac": "aabbccddeeff",
                "timestamp": int(time.time()),
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 1


@pytest.mark.asyncio
async def test_webhook_rejects_when_queue_full(client, fake_redis, sign_payload):
    """Webhook should return 429 when queue is at max depth."""
    from app.config import get_settings
    max_depth = get_settings().max_queue_depth
    for i in range(max_depth):
        await fake_redis.lpush("userid_queue", f"item-{i}")

    payload = {
        "topic": "client-join",
        "events": [
            {
                "client_username": "user@example.edu",
                "client_ip": "10.1.1.1",
                "mac": "aabbccddeeff",
                "timestamp": int(time.time()),
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 429
    assert "queue full" in resp.json()["error"]


@pytest.mark.asyncio
async def test_webhook_skips_loopback_ip(client, fake_redis, sign_payload):
    """Loopback IPs should be rejected."""
    payload = {
        "topic": "client-join",
        "events": [
            {
                "client_username": "user@example.edu",
                "client_ip": "127.0.0.1",
                "mac": "aabbccddeeff",
                "timestamp": int(time.time()),
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 0


@pytest.mark.asyncio
async def test_webhook_skips_multicast_ip(client, fake_redis, sign_payload):
    """Multicast IPs should be rejected."""
    payload = {
        "topic": "client-join",
        "events": [
            {
                "client_username": "user@example.edu",
                "client_ip": "224.0.0.1",
                "mac": "aabbccddeeff",
                "timestamp": int(time.time()),
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] == 0


@pytest.mark.asyncio
async def test_webhook_rejects_non_list_events(client, fake_redis, sign_payload):
    """Non-list events field should return 400."""
    payload = {
        "topic": "client-join",
        "events": "not_a_list",
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    resp = await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    assert resp.status_code == 400
