import json

import pytest

from app.metrics import (
    EVENTS_QUEUED,
    EVENTS_RECEIVED,
    EVENTS_REJECTED,
)


def _counter_value(counter, labels=None):
    """Get current value of a counter (with optional labels)."""
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_format(client, fake_redis):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert b"mist_userid_events_received_total" in resp.content


@pytest.mark.asyncio
async def test_events_received_increments(client, fake_redis, sample_client_join_event, sign_payload):
    before = _counter_value(EVENTS_RECEIVED, {"topic": "client-join"})
    body = json.dumps(sample_client_join_event).encode()
    sig = sign_payload(body)
    await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    after = _counter_value(EVENTS_RECEIVED, {"topic": "client-join"})
    assert after - before == 1


@pytest.mark.asyncio
async def test_events_queued_increments(client, fake_redis, sample_client_join_event, sign_payload):
    before = _counter_value(EVENTS_QUEUED)
    body = json.dumps(sample_client_join_event).encode()
    sig = sign_payload(body)
    await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    after = _counter_value(EVENTS_QUEUED)
    assert after - before == 1


@pytest.mark.asyncio
async def test_events_rejected_no_username(client, fake_redis, sign_payload):
    before = _counter_value(EVENTS_REJECTED, {"reason": "no_username"})
    payload = {
        "topic": "client-join",
        "events": [{"client_ip": "10.1.1.1", "mac": "aabbccddeeff"}],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    after = _counter_value(EVENTS_REJECTED, {"reason": "no_username"})
    assert after - before == 1


@pytest.mark.asyncio
async def test_events_rejected_no_ip(client, fake_redis, sign_payload):
    before = _counter_value(EVENTS_REJECTED, {"reason": "no_ip"})
    payload = {
        "topic": "client-join",
        "events": [{"client_username": "user@example.edu", "mac": "aabbccddeeff"}],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    after = _counter_value(EVENTS_REJECTED, {"reason": "no_ip"})
    assert after - before == 1


@pytest.mark.asyncio
async def test_events_rejected_invalid_ip(client, fake_redis, sign_payload):
    before = _counter_value(EVENTS_REJECTED, {"reason": "invalid_ip"})
    payload = {
        "topic": "client-join",
        "events": [
            {"client_username": "user@example.edu", "client_ip": "169.254.1.1", "mac": "aa"}
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    after = _counter_value(EVENTS_REJECTED, {"reason": "invalid_ip"})
    assert after - before == 1


@pytest.mark.asyncio
async def test_multiple_events_increments(client, fake_redis, sign_payload):
    before_received = _counter_value(EVENTS_RECEIVED, {"topic": "client-sessions"})
    before_queued = _counter_value(EVENTS_QUEUED)
    payload = {
        "topic": "client-sessions",
        "events": [
            {
                "client_username": "user1@example.edu",
                "client_ip": "10.1.1.1",
                "next_ap": "020000000a07",
            },
            {
                "client_username": "user2@example.edu",
                "client_ip": "10.1.1.2",
                "next_ap": "000000000000",
            },
        ],
    }
    body = json.dumps(payload).encode()
    sig = sign_payload(body)
    await client.post(
        "/mist/webhook",
        content=body,
        headers={"X-Mist-Signature-v2": sig},
    )
    after_received = _counter_value(EVENTS_RECEIVED, {"topic": "client-sessions"})
    after_queued = _counter_value(EVENTS_QUEUED)
    assert after_received - before_received == 2
    assert after_queued - before_queued == 2
