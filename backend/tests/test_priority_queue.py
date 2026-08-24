"""Priority tier as an ordered, capped queue (todo item 10)."""

from types import SimpleNamespace

import cache
import prefetch
import telegram
from config import BLOCK_SIZE


def test_second_set_priority_puts_new_id_at_front_and_keeps_first():
    prefetch.clear_priority()

    prefetch.set_priority("test", 1)
    prefetch.set_priority("test", 2)

    assert prefetch._priority_queue == [("test", 2), ("test", 1)]


def test_requeuing_existing_id_moves_to_front_without_duplicating():
    prefetch.clear_priority()
    prefetch.set_priority("test", 1)
    prefetch.set_priority("test", 2)

    prefetch.set_priority("test", 1)

    assert prefetch._priority_queue == [("test", 1), ("test", 2)]


def test_queue_cap_drops_from_tail():
    prefetch.clear_priority()

    for msg_id in range(1, prefetch.PRIORITY_QUEUE_CAP + 2):
        prefetch.set_priority("test", msg_id)

    ids = [msg_id for _, msg_id in prefetch._priority_queue]
    assert len(ids) == prefetch.PRIORITY_QUEUE_CAP
    assert ids[0] == prefetch.PRIORITY_QUEUE_CAP + 1
    assert 1 not in ids


def test_clear_priority_empties_whole_queue():
    prefetch.clear_priority()
    prefetch.set_priority("test", 1)
    prefetch.set_priority("test", 2)

    prefetch.clear_priority()

    assert prefetch._priority_queue == []


async def test_select_priority_job_skips_fully_cached_front_entry(
    tmp_path, monkeypatch
):
    install_world(tmp_path, monkeypatch)
    cached_msg = make_msg(1, BLOCK_SIZE)
    next_msg = make_msg(2, BLOCK_SIZE)
    cache.write_block("test", 1, 0, b"x" * BLOCK_SIZE)
    messages = {1: cached_msg, 2: next_msg}

    async def get_message(msg_id):
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_priority("test", 2)
    prefetch.set_priority("test", 1)  # front is now 1 (fully cached)

    job = await prefetch.select_priority_job()

    assert job == ("test", next_msg, 0)
    assert prefetch._priority_queue == [("test", 2)]


async def test_select_priority_job_skips_oversized_front_entry(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    oversized_msg = make_msg(1, cache.MAX_BYTES + 1)
    next_msg = make_msg(2, BLOCK_SIZE)
    messages = {1: oversized_msg, 2: next_msg}

    async def get_message(msg_id):
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_priority("test", 2)
    prefetch.set_priority("test", 1)

    job = await prefetch.select_priority_job()

    assert job == ("test", next_msg, 0)
    assert prefetch._priority_queue == [("test", 2)]


async def test_select_priority_job_skips_unresolvable_front_entry(
    tmp_path, monkeypatch
):
    install_world(tmp_path, monkeypatch)
    next_msg = make_msg(2, BLOCK_SIZE)

    async def get_message(msg_id):
        return None if msg_id == 1 else next_msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_priority("test", 2)
    prefetch.set_priority("test", 1)

    job = await prefetch.select_priority_job()

    assert job == ("test", next_msg, 0)
    assert prefetch._priority_queue == [("test", 2)]


async def test_select_priority_job_skips_stale_channel_front_entry(
    tmp_path, monkeypatch
):
    install_world(tmp_path, monkeypatch)
    next_msg = make_msg(2, BLOCK_SIZE)

    async def get_message(msg_id):
        return next_msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_priority("test", 2)
    prefetch.set_priority("other", 1)  # stored under a channel that isn't active

    job = await prefetch.select_priority_job()

    assert job == ("test", next_msg, 0)
    assert prefetch._priority_queue == [("test", 2)]


# --- helpers ---


def install_world(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache, "MAX_BYTES", 10**12)
    monkeypatch.setattr(cache, "_total_bytes", None)
    prefetch._block_locks.clear()
    prefetch._urgent_keys.clear()
    prefetch._urgent_empty.set()
    prefetch._work_available.clear()
    prefetch._pin = None
    prefetch.clear_priority()
    prefetch._logged_oversized_pins.clear()
    prefetch.reset_visible_pass()
    prefetch.reset_prewarm_pass()
    prefetch.clear_batch()


def make_msg(msg_id, file_size):
    return SimpleNamespace(
        id=msg_id,
        file=SimpleNamespace(size=file_size),
        media=object(),
    )
