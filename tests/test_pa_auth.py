import os
from unittest.mock import AsyncMock

import httpx
import pytest

from app import pa_auth
from app.pa_auth import generate_api_key, get_api_key, invalidate_api_key


@pytest.fixture(autouse=True)
def reset_cached_key():
    """Reset cached API key between tests."""
    pa_auth._cached_api_key = None
    yield
    pa_auth._cached_api_key = None


class TestGenerateApiKey:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_resp = httpx.Response(
            200,
            text='''<response status="success">
                <result>
                    <key>LUFRPT14MW5xOEo1R09KVlBZNnpnemh0VHRBOGJGTlk9N0d</key>
                </result>
            </response>''',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        key = await generate_api_key(
            client, "https://pa.example.com", "admin", "password"
        )
        assert key == "LUFRPT14MW5xOEo1R09KVlBZNnpnemh0VHRBOGJGTlk9N0d"
        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == "https://pa.example.com/api/"
        assert call_args[1]["data"]["type"] == "keygen"
        assert call_args[1]["data"]["user"] == "admin"
        assert call_args[1]["data"]["password"] == "password"

    @pytest.mark.asyncio
    async def test_invalid_credentials(self):
        mock_resp = httpx.Response(
            200,
            text='''<response status="error">
                <msg><line>Invalid credentials</line></msg>
            </response>''',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="Keygen failed"):
            await generate_api_key(
                client, "https://pa.example.com", "admin", "wrong"
            )

    @pytest.mark.asyncio
    async def test_http_error(self):
        mock_resp = httpx.Response(500, text="Internal Server Error")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="Keygen HTTP 500"):
            await generate_api_key(
                client, "https://pa.example.com", "admin", "password"
            )

    @pytest.mark.asyncio
    async def test_invalid_xml_response(self):
        mock_resp = httpx.Response(200, text="not xml at all")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="Invalid XML"):
            await generate_api_key(
                client, "https://pa.example.com", "admin", "password"
            )

    @pytest.mark.asyncio
    async def test_missing_key_in_response(self):
        mock_resp = httpx.Response(
            200,
            text='<response status="success"><result></result></response>',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="No API key"):
            await generate_api_key(
                client, "https://pa.example.com", "admin", "password"
            )


class TestGetApiKey:
    @pytest.mark.asyncio
    async def test_returns_static_key_if_configured(self):
        """If PA_API_KEY is set, return it directly without keygen."""
        os.environ["PA_API_KEY"] = "static-key-123"
        from app import config
        config._settings = None

        client = AsyncMock(spec=httpx.AsyncClient)
        key = await get_api_key(client)

        assert key == "static-key-123"
        client.post.assert_not_called()

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        config._settings = None

    @pytest.mark.asyncio
    async def test_generates_key_when_no_static_key(self):
        """If PA_API_KEY is empty, generate from first target."""
        os.environ["PA_API_KEY"] = ""
        os.environ["PA_USERNAME"] = "admin"
        os.environ["PA_PASSWORD"] = "secret"
        os.environ["PA_TARGETS"] = "https://pa1.example.com,https://pa2.example.com"
        from app import config
        config._settings = None

        mock_resp = httpx.Response(
            200,
            text='<response status="success"><result><key>generated-key</key></result></response>',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        key = await get_api_key(client)

        assert key == "generated-key"
        assert client.post.call_count == 1  # Only called first target

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        os.environ["PA_TARGETS"] = "https://pa-test.example.com"
        del os.environ["PA_USERNAME"]
        del os.environ["PA_PASSWORD"]
        config._settings = None

    @pytest.mark.asyncio
    async def test_caches_generated_key(self):
        """Generated key should be cached for subsequent calls."""
        os.environ["PA_API_KEY"] = ""
        os.environ["PA_USERNAME"] = "admin"
        os.environ["PA_PASSWORD"] = "secret"
        from app import config
        config._settings = None

        mock_resp = httpx.Response(
            200,
            text='<response status="success"><result><key>cached-key</key></result></response>',
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        # First call generates
        key1 = await get_api_key(client)
        # Second call uses cache
        key2 = await get_api_key(client)

        assert key1 == "cached-key"
        assert key2 == "cached-key"
        assert client.post.call_count == 1  # Only one keygen call

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        del os.environ["PA_USERNAME"]
        del os.environ["PA_PASSWORD"]
        config._settings = None

    @pytest.mark.asyncio
    async def test_tries_next_target_on_failure(self):
        """If first target fails, try next one."""
        os.environ["PA_API_KEY"] = ""
        os.environ["PA_USERNAME"] = "admin"
        os.environ["PA_PASSWORD"] = "secret"
        os.environ["PA_TARGETS"] = "https://pa1.example.com,https://pa2.example.com"
        from app import config
        config._settings = None

        responses = [
            httpx.ConnectError("Connection refused"),
            httpx.Response(
                200,
                text='<response status="success"><result><key>key-from-pa2</key></result></response>',
            ),
        ]
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=responses)

        key = await get_api_key(client)

        assert key == "key-from-pa2"
        assert client.post.call_count == 2

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        os.environ["PA_TARGETS"] = "https://pa-test.example.com"
        del os.environ["PA_USERNAME"]
        del os.environ["PA_PASSWORD"]
        config._settings = None

    @pytest.mark.asyncio
    async def test_raises_if_all_targets_fail(self):
        """Raise RuntimeError if no target can generate a key."""
        os.environ["PA_API_KEY"] = ""
        os.environ["PA_USERNAME"] = "admin"
        os.environ["PA_PASSWORD"] = "secret"
        os.environ["PA_TARGETS"] = "https://pa1.example.com,https://pa2.example.com"
        from app import config
        config._settings = None

        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with pytest.raises(RuntimeError, match="Could not generate API key"):
            await get_api_key(client)

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        os.environ["PA_TARGETS"] = "https://pa-test.example.com"
        del os.environ["PA_USERNAME"]
        del os.environ["PA_PASSWORD"]
        config._settings = None


class TestInvalidateApiKey:
    def test_clears_cached_key(self):
        pa_auth._cached_api_key = "some-cached-key"
        invalidate_api_key()
        assert pa_auth._cached_api_key is None

    def test_noop_when_no_cached_key(self):
        pa_auth._cached_api_key = None
        invalidate_api_key()  # Should not raise
        assert pa_auth._cached_api_key is None


class TestSendToTargetWithKeyRefresh:
    """Test 401 handling with automatic key refresh in paloalto.py."""

    @pytest.mark.asyncio
    async def test_401_triggers_key_refresh(self):
        """On 401, should invalidate key, regenerate, and retry."""
        os.environ["PA_API_KEY"] = ""
        os.environ["PA_USERNAME"] = "admin"
        os.environ["PA_PASSWORD"] = "secret"
        from app import config
        config._settings = None

        # Set initial cached key
        pa_auth._cached_api_key = "old-invalid-key"

        from app.paloalto import send_to_target

        responses = [
            # First request with old key fails
            httpx.Response(401, text="Unauthorized"),
            # Keygen request succeeds
            httpx.Response(
                200,
                text='<response status="success"><result><key>new-key</key></result></response>',
            ),
            # Retry with new key succeeds
            httpx.Response(200, text='<response status="success"/>'),
        ]
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=responses)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "old-invalid-key", max_retries=3
        )

        assert result is True
        assert client.post.call_count == 3
        assert pa_auth._cached_api_key == "new-key"

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        del os.environ["PA_USERNAME"]
        del os.environ["PA_PASSWORD"]
        config._settings = None

    @pytest.mark.asyncio
    async def test_401_after_refresh_fails_permanently(self):
        """If 401 persists after refresh, should fail without infinite loop."""
        os.environ["PA_API_KEY"] = ""
        os.environ["PA_USERNAME"] = "admin"
        os.environ["PA_PASSWORD"] = "secret"
        from app import config
        config._settings = None

        pa_auth._cached_api_key = "old-key"

        from app.paloalto import send_to_target

        responses = [
            # First request fails
            httpx.Response(401, text="Unauthorized"),
            # Keygen succeeds
            httpx.Response(
                200,
                text='<response status="success"><result><key>new-key</key></result></response>',
            ),
            # Retry still fails
            httpx.Response(401, text="Still Unauthorized"),
        ]
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=responses)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "old-key", max_retries=3
        )

        assert result is False
        assert client.post.call_count == 3

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        del os.environ["PA_USERNAME"]
        del os.environ["PA_PASSWORD"]
        config._settings = None

    @pytest.mark.asyncio
    async def test_no_refresh_when_using_static_key(self):
        """With static PA_API_KEY, 401 should fail immediately (no keygen possible)."""
        os.environ["PA_API_KEY"] = "static-key"
        from app import config
        config._settings = None

        from app.paloalto import send_to_target

        responses = [
            httpx.Response(401, text="Unauthorized"),
            # No keygen needed - static key is returned
            httpx.Response(401, text="Still Unauthorized"),
        ]
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=responses)

        result = await send_to_target(
            client, "https://pa.example.com", "<xml/>", "static-key", max_retries=3
        )

        # With static key, 401 triggers refresh attempt, but get_api_key returns
        # the same static key, and retry still fails
        assert result is False

        # Reset
        os.environ["PA_API_KEY"] = "test-api-key"
        config._settings = None
