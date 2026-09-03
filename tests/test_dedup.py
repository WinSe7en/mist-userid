import pytest

from app.dedup import _make_dedup_key, is_duplicate


@pytest.mark.asyncio
async def test_first_seen_not_duplicate(fake_redis):
    result = await is_duplicate("user@example.edu", "10.1.1.1")
    assert result is False


@pytest.mark.asyncio
async def test_second_seen_is_duplicate(fake_redis):
    await is_duplicate("user@example.edu", "10.1.1.1")
    result = await is_duplicate("user@example.edu", "10.1.1.1")
    assert result is True


@pytest.mark.asyncio
async def test_different_users_not_duplicate(fake_redis):
    await is_duplicate("user1@example.edu", "10.1.1.1")
    result = await is_duplicate("user2@example.edu", "10.1.1.1")
    assert result is False


@pytest.mark.asyncio
async def test_different_ips_not_duplicate(fake_redis):
    await is_duplicate("user@example.edu", "10.1.1.1")
    result = await is_duplicate("user@example.edu", "10.1.1.2")
    assert result is False


@pytest.mark.asyncio
async def test_dedup_key_format(fake_redis):
    # @ is URL-encoded as %40
    await is_duplicate("user@example.edu", "10.1.1.1")
    exists = await fake_redis.exists("dedup:user%40example.edu:10.1.1.1")
    assert exists == 1


@pytest.mark.asyncio
async def test_dedup_key_has_ttl(fake_redis):
    await is_duplicate("user@example.edu", "10.1.1.1")
    ttl = await fake_redis.ttl("dedup:user%40example.edu:10.1.1.1")
    assert 0 < ttl <= 300


class TestMakeDedupKey:
    def test_basic_email(self):
        key = _make_dedup_key("user@example.edu", "10.1.1.1")
        assert key == "dedup:user%40example.edu:10.1.1.1"

    def test_colon_in_username_no_collision(self):
        # These should produce different keys
        key1 = _make_dedup_key("user:10.1.1.1", "10.2.2.2")
        key2 = _make_dedup_key("user", "10.1.1.1:10.2.2.2")
        assert key1 != key2

    def test_special_chars_encoded(self):
        key = _make_dedup_key("user:name@example.edu", "10.1.1.1")
        # : becomes %3A, @ becomes %40
        assert key == "dedup:user%3Aname%40example.edu:10.1.1.1"

    def test_simple_username(self):
        key = _make_dedup_key("admin", "10.1.1.1")
        assert key == "dedup:admin:10.1.1.1"
