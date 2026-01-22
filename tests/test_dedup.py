import pytest

from app.dedup import is_duplicate


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
    await is_duplicate("user@example.edu", "10.1.1.1")
    exists = await fake_redis.exists("dedup:user@example.edu:10.1.1.1")
    assert exists == 1


@pytest.mark.asyncio
async def test_dedup_key_has_ttl(fake_redis):
    await is_duplicate("user@example.edu", "10.1.1.1")
    ttl = await fake_redis.ttl("dedup:user@example.edu:10.1.1.1")
    assert 0 < ttl <= 300
