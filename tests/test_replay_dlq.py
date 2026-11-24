import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import replay_dlq  # noqa: E402

from app.paloalto import DLQ_KEY  # noqa: E402


def _entry(age: float, now: float, user: str = "a@example.edu") -> str:
    return json.dumps({
        "timestamp": now - age,
        "targets": ["https://pa-test.example.com"],
        "logins": [[user, "10.1.1.1"]],
        "logouts": [],
        "error": "x",
    })


def test_select_entries_splits_by_age_and_skips_garbage():
    now = 1_000_000.0
    raws = [_entry(10, now), _entry(301, now), "not json", json.dumps({"no": "ts"})]
    fresh, skipped = replay_dlq.select_entries(raws, 300, now)
    assert [raw for raw, _ in fresh] == [raws[0]]
    assert skipped == 3


@pytest.mark.asyncio
async def test_replay_sends_fresh_and_leaves_stale(fake_redis):
    now = time.time()
    fresh, stale = _entry(10, now, "fresh@example.edu"), _entry(3600, now, "old@example.edu")
    await fake_redis.lpush(DLQ_KEY, stale, fresh)

    with patch.object(replay_dlq, "send_batch", AsyncMock(return_value=[])) as sb, \
         patch.object(replay_dlq, "close_redis", AsyncMock()):
        rc = await replay_dlq.replay(max_age=300, dry_run=False)

    assert rc == 0
    sb.assert_awaited_once()
    assert sb.await_args.args[1] == [("fresh@example.edu", "10.1.1.1")]
    assert await fake_redis.lrange(DLQ_KEY, 0, -1) == [stale]


@pytest.mark.asyncio
async def test_dry_run_sends_nothing(fake_redis):
    await fake_redis.lpush(DLQ_KEY, _entry(10, time.time()))
    with patch.object(replay_dlq, "send_batch", AsyncMock()) as sb, \
         patch.object(replay_dlq, "close_redis", AsyncMock()):
        await replay_dlq.replay(max_age=300, dry_run=True)
    sb.assert_not_awaited()
    assert await fake_redis.llen(DLQ_KEY) == 1
