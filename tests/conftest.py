import hashlib
import hmac
import json
import os

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Set test env vars before importing app modules
os.environ.update({
    "PA_TARGETS": "https://pa-test.example.com",
    "PA_API_KEY": "test-api-key",
    "MIST_WEBHOOK_SECRET": "test-secret",
    "REDIS_URL": "redis://localhost:6379",
    "BATCH_SIZE": "50",
    "BATCH_FLUSH_INTERVAL": "2",
    "DEDUP_TTL": "300",
    "MAX_RETRY_ATTEMPTS": "3",
    "USERID_TIMEOUT": "60",
    "LOG_LEVEL": "DEBUG",
})


@pytest.fixture(autouse=True)
def reset_settings():
    """Reset cached settings between tests."""
    from app import config
    config._settings = None
    yield
    config._settings = None


@pytest_asyncio.fixture
async def fake_redis(monkeypatch):
    """Provide a fake Redis instance for tests."""
    from app import redis_client
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_client._pool = fake
    yield fake
    await fake.aclose()
    redis_client._pool = None


@pytest.fixture
def client(fake_redis):
    """Provide a test HTTP client for the FastAPI app."""
    from app.main import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def webhook_secret():
    return "test-secret"


@pytest.fixture
def sign_payload(webhook_secret):
    """Helper to sign a webhook payload."""
    def _sign(body: bytes) -> str:
        return hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return _sign


@pytest.fixture
def sample_client_join_event():
    return {
        "topic": "client-join",
        "events": [
            {
                "ap": "020000000a02",
                "ap_name": "north-1-156",
                "band": "24",
                "bssid": "020000000a08",
                "client_hostname": "Test-Laptop",
                "client_ip": "10.5.72.249",
                "client_username": "alex.taylor@example.edu",
                "connect": 1769121852,
                "connect_float": 1769121852.22,
                "mac": "020000000c04",
                "org_id": "00000000-0000-4000-8000-000000000001",
                "random_mac": False,
                "rssi": -51,
                "site_id": "00000000-0000-4000-8000-000000000002",
                "site_name": "North_Hall",
                "ssid": "eduroam",
                "timestamp": 1769121852,
                "version": 2,
                "wlan_id": "00000000-0000-4000-8000-000000000003",
            }
        ],
    }


@pytest.fixture
def sample_client_session_roam():
    return {
        "topic": "client-sessions",
        "events": [
            {
                "ap": "020000000a06",
                "ap_name": "South-173",
                "client_ip": "10.7.71.140",
                "client_username": "casey.garcia-lopez@example.edu",
                "connect": 1769117271,
                "disconnect": 1769117307,
                "duration": 36.146,
                "mac": "020000000c02",
                "next_ap": "020000000a07",
                "org_id": "00000000-0000-4000-8000-000000000001",
                "random_mac": True,
                "ssid": "eduroam",
                "termination_reason": 3,
                "timestamp": 1769117307,
                "version": 2,
                "wlan_id": "00000000-0000-4000-8000-000000000003",
            }
        ],
    }


@pytest.fixture
def sample_client_session_disconnect():
    return {
        "topic": "client-sessions",
        "events": [
            {
                "ap": "020000000a04",
                "ap_name": "north-1-121d",
                "client_ip": "10.5.141.80",
                "client_username": "jordan.smith@example.edu",
                "connect": 1769117238,
                "disconnect": 1769117262,
                "duration": 24.54,
                "mac": "020000000c03",
                "next_ap": "000000000000",
                "org_id": "00000000-0000-4000-8000-000000000001",
                "random_mac": True,
                "ssid": "eduroam",
                "termination_reason": 1,
                "timestamp": 1769117262,
                "version": 2,
                "wlan_id": "00000000-0000-4000-8000-000000000003",
            }
        ],
    }


@pytest.fixture
def sample_psk_event():
    return {
        "topic": "client-join",
        "events": [
            {
                "ap": "020000000a05",
                "client_ip": "10.6.3.6",
                "mac": "020000000c07",
                "psk_id": "00000000-0000-4000-8000-000000000004",
                "psk_name": "Morgan.Riley@example.edu",
                "random_mac": True,
                "ssid": "Resident_Wireless",
                "timestamp": 1769121848,
                "version": 2,
                "wlan_id": "00000000-0000-4000-8000-000000000005",
            }
        ],
    }
