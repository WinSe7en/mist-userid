from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring

import httpx
import pytest

from app import pa_auth
from app.paloalto import DLQ_KEY, build_uid_xml, send_batch, send_to_target


@pytest.fixture(autouse=True)
def reset_cached_key():
    """Reset cached API key between tests."""
    pa_auth._cached_api_key = None
    yield
    pa_auth._cached_api_key = None


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
    async def test_401_attempts_key_refresh_then_fails(self):
        """401 triggers key refresh attempt; with static key, retry fails."""
        mock_resp = httpx.Response(401, text="Unauthorized")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "key", max_retries=3
        )
        assert result is False
        # First call fails with 401, triggers refresh (returns static key),
        # retry also fails with 401 (allow_key_refresh=False)
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_403_permanent_failure_no_retry(self):
        """Genuine 403 is a permanent failure with no retry."""
        mock_resp = httpx.Response(403, text="Forbidden")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "key", max_retries=3
        )
        assert result is False
        assert client.post.call_count == 1  # no retries for genuine 403

    @pytest.mark.asyncio
    async def test_403_commit_window_retries_then_succeeds(self):
        """403 with 'not authorized for user role' is transient (commit window)."""
        commit_403 = httpx.Response(
            403,
            text='<response status="error"><msg>'
                 '<line>Type [user-id] not authorized for user role</line>'
                 '</msg></response>',
        )
        success_resp = httpx.Response(
            200, text='<response status="success"><result/></response>'
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=[commit_403, success_resp])

        with patch("app.paloalto.asyncio.sleep", new_callable=AsyncMock):
            result = await send_to_target(
                client, "https://pa.example.com", "<xml/>", "key", max_retries=3
            )
        assert result is True
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_403_commit_window_max_retries_exhausted(self):
        """Commit-window 403 exhausts retries if commit takes too long."""
        commit_403 = httpx.Response(
            403,
            text='<response status="error"><msg>'
                 '<line>Type [user-id] not authorized for user role</line>'
                 '</msg></response>',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=commit_403)

        with patch("app.paloalto.asyncio.sleep", new_callable=AsyncMock):
            result = await send_to_target(
                client, "https://pa.example.com", "<xml/>", "key", max_retries=2
            )
        assert result is False
        assert client.post.call_count == 3  # initial + 2 retries

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

    @pytest.mark.asyncio
    async def test_delete_mapping_failed_is_benign(self):
        """Logout for non-existent user should be treated as success."""
        # This is the actual response format from PA when logout fails
        # because the user wasn't in the table (mapping timed out or never existed)
        mock_resp = httpx.Response(
            200,
            text='''<response status="error"><msg><line><uid-response>
  <version>2.0</version>
  <payload>
    <login>
    </login>
    <logout>
      <entry name="user@example.edu" ip="10.1.1.1" message="Delete mapping failed"/>
    </logout>
  </payload>
</uid-response></line></msg></response>''',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "key", max_retries=3
        )
        assert result is True  # Should be treated as success
        client.post.assert_called_once()  # No retries needed


    @pytest.mark.asyncio
    async def test_xml_unauth_session_timeout_triggers_key_refresh(self):
        """PAN-OS returns HTTP 200 with XML status='unauth' on session timeout.
        Should trigger key refresh and retry, not dead-letter."""
        unauth_resp = httpx.Response(
            200,
            text='<response status="unauth" code="22"><msg><line>Session timed out</line></msg></response>',
        )
        success_resp = httpx.Response(
            200,
            text='<response status="success"><result/></response>',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        # First call: session timeout, second call (after refresh): success
        client.post = AsyncMock(side_effect=[unauth_resp, success_resp])

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "key", max_retries=3
        )
        assert result is True
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_xml_unauth_no_refresh_if_already_refreshed(self):
        """If key was already refreshed and we still get unauth, fail permanently."""
        unauth_resp = httpx.Response(
            200,
            text='<response status="unauth" code="22"><msg><line>Session timed out</line></msg></response>',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=unauth_resp)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "key", max_retries=3,
            allow_key_refresh=False,
        )
        assert result is False
        assert client.post.call_count == 1


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

        # Use 403 which doesn't trigger key refresh
        mock_resp = httpx.Response(403, text="Forbidden")
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
