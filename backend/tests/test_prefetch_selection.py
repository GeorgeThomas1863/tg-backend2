"""Pure selection behavior for pinned and prewarm background jobs."""

import asyncio
import os
from types import SimpleNamespace

import cache
import channels
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


async def test_pin_outranks_priority(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    pin_msg = make_msg(1, BLOCK_SIZE)
    priority_msg = make_msg(2, BLOCK_SIZE)
    messages = {1: pin_msg, 2: priority_msg}

    async def get_message(msg_id):
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.note_playhead("test", 1, 0)
    prefetch.set_priority("test", 2)

    job = await prefetch.select_worker_job()

    assert job == ("test", pin_msg, 0)
    assert prefetch._active_tier == "pin"


async def test_priority_outranks_visible(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    priority_msg = make_msg(2, BLOCK_SIZE)
    visible_msg = make_msg(3, BLOCK_SIZE)
    messages = {2: priority_msg, 3: visible_msg}

    async def get_message(msg_id):
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_visible("test", [3])
    prefetch.set_priority("test", 2)

    job = await prefetch.select_worker_job()

    assert job == ("test", priority_msg, 0)
    assert prefetch._active_tier == "priority"


async def test_priority_block_selection_starts_at_zero(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    cache.write_block("test", 7, 0, b"x" * BLOCK_SIZE)

    selected = prefetch.select_priority_block("test", 7, 3 * BLOCK_SIZE)

    assert selected == 1


async def test_priority_slot_cleared_when_file_fully_cached(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(5, BLOCK_SIZE)
    cache.write_block("test", 5, 0, b"x" * BLOCK_SIZE)

    async def get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_priority("test", 5)

    assert await prefetch.select_priority_job() is None
    assert prefetch._priority is None


async def test_priority_oversized_file_skipped_and_cleared(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(6, cache.MAX_BYTES + 1)

    async def get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_priority("test", 6)

    assert await prefetch.select_priority_job() is None
    assert prefetch._priority is None


async def test_priority_cleared_when_stored_channel_is_stale(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(5, BLOCK_SIZE)

    async def get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(channels, "active_key", lambda: "other")
    prefetch.set_priority("test", 5)

    assert await prefetch.select_priority_job() is None
    assert prefetch._priority is None


async def test_pin_dropped_when_stored_channel_is_stale(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(7, BLOCK_SIZE)

    async def get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(channels, "active_key", lambda: "other")
    prefetch.note_playhead("test", 7, 0)

    assert await prefetch.select_pin_job() is None
    assert prefetch._pin is None


async def test_priority_cleared_when_message_fails_to_resolve(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)

    async def get_message(msg_id):
        return None

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_priority("test", 5)

    assert await prefetch.select_priority_job() is None
    assert prefetch._priority is None


async def test_complete_pin_falls_through_to_prewarm(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    msg = make_msg(7, BLOCK_SIZE)
    cache.write_block("test", 7, 0, b"x" * BLOCK_SIZE)
    prewarm_job = ("test", make_msg(9, BLOCK_SIZE), 0)

    async def get_message(msg_id):
        return msg

    async def select_prewarm_job(slot=0):
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


async def test_busy_visible_video_keeps_budget_for_owning_slot(monkeypatch):
    install_world(cache.CACHE_ROOT, monkeypatch)
    messages = {
        1: make_msg(1, 2 * BLOCK_SIZE),
        2: make_msg(2, BLOCK_SIZE),
        3: make_msg(3, BLOCK_SIZE),
    }
    monkeypatch.setattr(cache, "MAX_BYTES", 3 * BLOCK_SIZE)

    async def get_message(msg_id):
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.initialize_slots()
    prefetch._slot_msg_ids[0] = 1
    prefetch.set_visible("test", [1, 2, 3])

    slot_b_job = await prefetch.select_visible_job(1)
    slot_b_next_job = await prefetch.select_visible_job(1)
    slot_a_job = await prefetch.replan_current_visible_video("test", 1, 0)

    assert slot_b_job == ("test", messages[2], 0)
    assert slot_b_next_job is None
    assert slot_a_job == ("test", messages[1], 0)


async def test_visible_download_evicts_older_unseen_blocks_before_cached_visible_ones(
    tmp_path, monkeypatch
):
    # Regression: cache holds visible A (the oldest block) plus unrelated X,
    # and the cap fits exactly two blocks. Planning the visible set must refresh
    # A's LRU age so downloading visible B evicts X, not A — otherwise the
    # visible pass evicts a video the user is looking at.
    install_world(tmp_path, monkeypatch)
    messages = {1: make_msg(1, BLOCK_SIZE), 2: make_msg(2, BLOCK_SIZE)}
    monkeypatch.setattr(cache, "MAX_BYTES", 2 * BLOCK_SIZE)
    cache.write_block("test", 1, 0, b"a" * BLOCK_SIZE)
    cache.write_block("test", 9, 0, b"x" * BLOCK_SIZE)
    os.utime(cache.build_block_path("test", 1, 0), (1_000, 1_000))
    os.utime(cache.build_block_path("test", 9, 0), (2_000, 2_000))

    async def get_message(msg_id):
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.initialize_slots()
    prefetch.set_visible("test", [1, 2])

    job = await prefetch.select_visible_job(0)
    cache.write_block("test", 2, 0, b"b" * BLOCK_SIZE)

    assert job == ("test", messages[2], 0)
    assert cache.has_block("test", 1, 0)
    assert cache.has_block("test", 2, 0)
    assert not cache.has_block("test", 9, 0)


async def test_visible_lookup_does_not_reserve_for_stale_pass(monkeypatch):
    install_world(cache.CACHE_ROOT, monkeypatch)
    messages = {
        1: make_msg(1, BLOCK_SIZE),
        2: make_msg(2, BLOCK_SIZE),
    }
    monkeypatch.setattr(cache, "MAX_BYTES", BLOCK_SIZE)

    async def get_message(msg_id):
        if msg_id == 1:
            prefetch.set_visible("test", [2])
        return messages[msg_id]

    monkeypatch.setattr(telegram, "get_message", get_message)
    prefetch.set_visible("test", [1])

    selected_msg, blocks = await prefetch.find_next_visible_video()

    assert (selected_msg.id, blocks) == (2, [0])
    assert prefetch._visible_budget_ids == {2}


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
    prefetch._priority = None
    prefetch._worker_task = None
    prefetch._worker_download_task = None
    prefetch._worker_download_key = None
    prefetch._logged_oversized_pins.clear()
    prefetch._visible_channel = None
    prefetch._visible_ids = []
    prefetch.reset_visible_pass()
    prefetch.reset_prewarm_pass()


def make_msg(msg_id, file_size):
    return SimpleNamespace(
        id=msg_id,
        file=SimpleNamespace(size=file_size),
        media=object(),
    )
