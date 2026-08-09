"""Pure selection behavior for pinned and prewarm background jobs."""

import asyncio
from types import SimpleNamespace

import cache
import config
import prefetch
import telegram
from config import BLOCK_SIZE


def test_pin_order_starts_at_playhead_and_wraps():
    assert prefetch.select_missing_pin_block(3, 5, {3, 4, 0}) == 1


def test_pin_selection_skips_blocks_already_in_cache(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    cache.write_block("test", 7, 2, b"x" * BLOCK_SIZE)

    selected = prefetch.select_pin_block("test", 7, 2, 4 * BLOCK_SIZE)

    assert selected == 3


async def test_pin_selection_reads_latest_playhead_each_pick(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(7, 5 * BLOCK_SIZE)

    async def get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.note_playhead("test", 7, 1)
    first = await prefetch.select_pin_job()
    prefetch.note_playhead("test", 7, 4)

    second = await prefetch.select_pin_job()

    assert (first[2], second[2]) == (1, 4)


async def test_pin_selection_restarts_after_video_changes_during_lookup(
    tmp_path, monkeypatch
):
    install_world(tmp_path, monkeypatch)
    messages = {7: make_msg(7, 5 * BLOCK_SIZE), 8: make_msg(8, 5 * BLOCK_SIZE)}
    first_lookup = asyncio.Event()
    release_lookup = asyncio.Event()

    async def get_message(msg_id):
        if msg_id == 7:
            first_lookup.set()
            await release_lookup.wait()
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.note_playhead("test", 7, 1)
    selection = asyncio.create_task(prefetch.select_pin_job())
    await first_lookup.wait()
    prefetch.note_playhead("test", 8, 4)
    release_lookup.set()

    channel_key, msg, idx = await selection

    assert channel_key == "test"
    assert (msg.id, idx) == (8, 4)


async def test_pin_selection_uses_seek_during_message_lookup(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(7, 5 * BLOCK_SIZE)
    lookup_started = asyncio.Event()
    release_lookup = asyncio.Event()

    async def get_message(msg_id):
        lookup_started.set()
        await release_lookup.wait()
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.note_playhead("test", 7, 1)
    selection = asyncio.create_task(prefetch.select_pin_job())
    await lookup_started.wait()
    prefetch.note_playhead("test", 7, 4)
    release_lookup.set()

    assert (await selection)[2] == 4


async def test_oversized_pin_logs_once_across_calls(tmp_path, monkeypatch, capsys):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(7, cache.MAX_BYTES + 1)

    async def get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.note_playhead("test", 7, 0)

    assert await prefetch.select_pin_job() is None
    assert await prefetch.select_pin_job() is None

    notice = "PREFETCH pinned video 7 exceeds the cache cap"
    assert capsys.readouterr().out.count(notice) == 1


async def test_complete_pin_falls_through_to_prewarm(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(7, BLOCK_SIZE)
    cache.write_block("test", 7, 0, b"x" * BLOCK_SIZE)
    prewarm_job = (make_msg(9, BLOCK_SIZE), 0)

    async def get_message(msg_id):
        return msg

    async def select_prewarm_job():
        return prewarm_job

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(prefetch, "select_prewarm_job", select_prewarm_job)
    monkeypatch.setattr(config, "PREWARM_ENABLED", True)
    prefetch.note_playhead("test", 7, 0)

    assert await prefetch.select_worker_job() == prewarm_job


async def test_complete_pin_is_idle_when_prewarm_is_disabled(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(7, BLOCK_SIZE)
    cache.write_block("test", 7, 0, b"x" * BLOCK_SIZE)

    async def get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(config, "PREWARM_ENABLED", False)
    prefetch.note_playhead("test", 7, 0)

    assert await prefetch.select_worker_job() is None


async def test_prewarm_stops_when_uncached_remainder_would_cross_cap(
    tmp_path, monkeypatch
):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(12, BLOCK_SIZE)
    current = cache.current_total()
    monkeypatch.setattr(cache, "MAX_BYTES", current + BLOCK_SIZE - 1)

    async def take_next_video():
        return {"id": 12}

    async def resolve_prewarm_message(msg_id):
        return msg

    monkeypatch.setattr(prefetch, "take_next_video", take_next_video)
    monkeypatch.setattr(prefetch, "resolve_prewarm_message", resolve_prewarm_message)

    assert await prefetch.find_next_prewarm_video() is None
    assert prefetch._prewarm_is_last_page is True


async def test_prewarm_cap_counts_only_partially_cached_remainder(
    tmp_path, monkeypatch
):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(12, 2 * BLOCK_SIZE + 100)
    cache.write_block("test", 12, 0, b"x" * BLOCK_SIZE)
    current = cache.current_total()
    monkeypatch.setattr(cache, "MAX_BYTES", current + BLOCK_SIZE + 100)

    async def take_next_video():
        return {"id": 12}

    async def resolve_prewarm_message(msg_id):
        return msg

    monkeypatch.setattr(prefetch, "take_next_video", take_next_video)
    monkeypatch.setattr(prefetch, "resolve_prewarm_message", resolve_prewarm_message)

    selected_msg, blocks = await prefetch.find_next_prewarm_video()

    assert (selected_msg.id, blocks) == (12, [1, 2])


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
    prefetch._worker_task = None
    prefetch._worker_download_task = None
    prefetch._worker_download_key = None
    prefetch._logged_oversized_pins.clear()
    prefetch.reset_prewarm_pass()


def make_msg(msg_id, file_size):
    return SimpleNamespace(
        id=msg_id,
        file=SimpleNamespace(size=file_size),
        media=object(),
    )
