import asyncio
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring

import httpx
import pytest

from app.paloalto import DLQ_KEY, build_uid_xml, send_batch, send_to_target


class TestBuildUidXml:
    def test_logins_only(self):
        xml = build_uid_xml(
            logins=[("user@example.edu", "10.1.1.1")],
            logouts=[],
            timeout=60,
        )
        root = fromstring(xml)
        assert root.tag == "uid-message"
        assert root.find("type").text == "update"
        login_entries = root.findall(".//login/entry")
        assert len(login_entries) == 1
        assert login_entries[0].get("name") == "user@example.edu"
        assert login_entries[0].get("ip") == "10.1.1.1"
        assert login_entries[0].get("timeout") == "60"

    def test_logouts_only(self):
        xml = build_uid_xml(
            logins=[],
            logouts=[("user@example.edu", "10.1.1.1")],
            timeout=60,
        )
        root = fromstring(xml)
        logout_entries = root.findall(".//logout/entry")
        assert len(logout_entries) == 1
        assert logout_entries[0].get("name") == "user@example.edu"
        assert logout_entries[0].get("ip") == "10.1.1.1"
        assert logout_entries[0].get("timeout") is None

    def test_mixed_logins_and_logouts(self):
        xml = build_uid_xml(
            logins=[("user1@example.edu", "10.1.1.1"), ("user2@example.edu", "10.1.1.2")],
            logouts=[("user3@example.edu", "10.1.1.3")],
            timeout=45,
        )
        root = fromstring(xml)
        login_entries = root.findall(".//login/entry")
        logout_entries = root.findall(".//logout/entry")
        assert len(login_entries) == 2
        assert len(logout_entries) == 1
        assert login_entries[0].get("timeout") == "45"

    def test_empty_batch(self):
        xml = build_uid_xml(logins=[], logouts=[], timeout=60)
        root = fromstring(xml)
        assert root.find("type").text == "update"
        assert root.findall(".//login/entry") == []
        assert root.findall(".//logout/entry") == []


class TestSendToTarget:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_resp = httpx.Response(
            200,
            text='<response status="success"><result/></response>',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "key", max_retries=3
        )
        assert result is True
        client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_permanent_failure_no_retry(self):
        mock_resp = httpx.Response(401, text="Unauthorized")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "key", max_retries=3
        )
        assert result is False
        assert client.post.call_count == 1  # no retries

    @pytest.mark.asyncio
    async def test_transient_failure_retries(self):
        responses = [
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(200, text='<response status="success"/>'),
        ]
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=responses)

        with patch("app.paloalto.asyncio.sleep", new_callable=AsyncMock):
            result = await send_to_target(
                client, "https://pa.example.com", "<xml/>", "key", max_retries=3
            )
        assert result is True
        assert client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_connection_error_retries(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.Response(200, text='<response status="success"/>'),
            ]
        )

        with patch("app.paloalto.asyncio.sleep", new_callable=AsyncMock):
            result = await send_to_target(
                client, "https://pa.example.com", "<xml/>", "key", max_retries=3
            )
        assert result is True
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(
            side_effect=httpx.TimeoutException("Timeout")
        )

        with patch("app.paloalto.asyncio.sleep", new_callable=AsyncMock):
            result = await send_to_target(
                client, "https://pa.example.com", "<xml/>", "key", max_retries=2
            )
        assert result is False
        assert client.post.call_count == 3  # initial + 2 retries


class TestSendBatch:
    @pytest.mark.asyncio
    async def test_empty_batch_noop(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        result = await send_batch(client, [], [])
        assert result == []
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_to_all_targets(self):
        import os
        os.environ["PA_TARGETS"] = "https://pa1.example.com,https://pa2.example.com"
        from app import config
        config._settings = None

        mock_resp = httpx.Response(200, text='<response status="success"/>')
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        result = await send_batch(
            client,
            logins=[("user@example.edu", "10.1.1.1")],
            logouts=[],
        )
        assert result == []
        assert client.post.call_count == 2

        # Reset
        os.environ["PA_TARGETS"] = "https://pa-test.example.com"
        config._settings = None

    @pytest.mark.asyncio
    async def test_dead_letters_on_failure(self, fake_redis):
        import os
        os.environ["PA_TARGETS"] = "https://pa-fail.example.com"
        from app import config
        config._settings = None

        mock_resp = httpx.Response(401, text="Unauthorized")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        result = await send_batch(
            client,
            logins=[("user@example.edu", "10.1.1.1")],
            logouts=[],
        )
        assert result == ["https://pa-fail.example.com"]

        # Verify DLQ entry was written
        dlq_len = await fake_redis.llen(DLQ_KEY)
        assert dlq_len == 1

        # Reset
        os.environ["PA_TARGETS"] = "https://pa-test.example.com"
        config._settings = None
