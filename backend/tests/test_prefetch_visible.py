import asyncio
from types import SimpleNamespace

import cache
import config
import downloader
import prefetch
import telegram


async def test_visible_downloads_in_display_order_before_prewarm(
    monkeypatch, tmp_path
):
    calls = install_fakes(monkeypatch, tmp_path, [[make_video(9)]], {9: 1, 5: 2, 3: 1})
    prefetch.set_visible("test", [5, 3])

    await run_until_idle()

    assert calls["downloads"] == [(5, 0), (5, 1), (3, 0), (9, 0)]


async def test_visible_skips_cached_blocks_and_missing_messages(
    monkeypatch, tmp_path
):
    calls = install_fakes(monkeypatch, tmp_path, [[]], {5: 3, 4: None, 3: 1})
    cache.write_block("test", 5, 1, b"x")
    prefetch.set_visible("test", [5, 4, 3])

    await run_until_idle()

    assert calls["messages"] == [5, 4, 3]
    assert calls["downloads"] == [(5, 0), (5, 2), (3, 0)]


async def test_new_visible_list_replaces_old_mid_pass(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, tmp_path, [[]], {5: 3, 7: 1})
    prefetch.set_visible("test", [5])

    job = await prefetch.select_worker_job()
    await prefetch.run_worker_download(*job)
    prefetch.set_visible("test", [7])
    await run_until_idle()

    assert calls["downloads"] == [(5, 0), (7, 0)]


async def test_visible_skips_video_that_would_evict_other_visible_videos(
    monkeypatch, tmp_path
):
    calls = install_fakes(monkeypatch, tmp_path, [[]], {5: 2, 3: 2}, max_bytes=3)
    prefetch.set_visible("test", [5, 3])

    await run_until_idle()

    assert calls["downloads"] == [(5, 0), (5, 1)]


async def test_visible_skips_video_larger_than_the_whole_cap(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, tmp_path, [[]], {8: 5}, max_bytes=3)
    prefetch.set_visible("test", [8])

    await run_until_idle()

    assert calls["downloads"] == []


async def test_visible_list_for_inactive_channel_is_ignored(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, tmp_path, [[]], {5: 1})
    prefetch.set_visible("other", [5])

    await run_until_idle()

    assert calls["messages"] == []
    assert calls["downloads"] == []


async def test_pin_outranks_visible(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, tmp_path, [[]], {7: 2, 5: 1})
    prefetch.note_playhead("test", 7, 0)
    prefetch.set_visible("test", [5])

    await run_until_idle()

    assert calls["downloads"] == [(7, 0), (7, 1), (5, 0)]


async def test_visible_runs_even_with_prewarm_disabled(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, tmp_path, [[make_video(9)]], {9: 1, 5: 1})
    monkeypatch.setattr(prefetch.config, "PREWARM_ENABLED", False)
    prefetch.set_visible("test", [5])

    await run_until_idle()

    assert calls["downloads"] == [(5, 0)]
    assert calls["lists"] == []


async def test_set_visible_cancels_inflight_prewarm_download(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, tmp_path, [[make_video(9)]], {9: 1, 5: 1})
    prewarm_started = asyncio.Event()
    release = asyncio.Event()
    fast_download = downloader.download_block

    async def slow_prewarm_download(msg, idx):
        if msg.id == 9:
            prewarm_started.set()
            await release.wait()
        return await fast_download(msg, idx)

    monkeypatch.setattr(downloader, "download_block", slow_prewarm_download)
    worker = asyncio.create_task(prefetch.run_worker())
    try:
        await asyncio.wait_for(prewarm_started.wait(), timeout=1)
        prefetch.set_visible("test", [5])
        for _ in range(200):
            if (5, 0) in calls["downloads"]:
                break
            await asyncio.sleep(0)
        assert calls["downloads"] == [(5, 0)]
    finally:
        release.set()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


# --- helpers ---


def make_video(msg_id):
    return {"id": msg_id}


def make_message(msg_id, size):
    if size is None:
        return None
    return SimpleNamespace(id=msg_id, file=SimpleNamespace(size=size))


def install_fakes(monkeypatch, tmp_path, pages, sizes, max_bytes=10_000):
    calls = {"lists": [], "messages": [], "downloads": []}

    async def fake_list_videos(limit, before_id):
        calls["lists"].append((limit, before_id))
        page_index = len(calls["lists"]) - 1
        return pages[min(page_index, len(pages) - 1)]

    async def fake_get_message(msg_id):
        calls["messages"].append(msg_id)
        return make_message(msg_id, sizes.get(msg_id))

    async def fake_download_block(msg, idx):
        calls["downloads"].append((msg.id, idx))
        return bytes([msg.id % 256])

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache, "MAX_BYTES", max_bytes)
    monkeypatch.setattr(config, "BLOCK_SIZE", 1)
    monkeypatch.setattr(prefetch.config, "PREWARM_ENABLED", True)
    monkeypatch.setattr(telegram, "list_videos", fake_list_videos)
    monkeypatch.setattr(telegram, "get_message", fake_get_message)
    monkeypatch.setattr(downloader, "download_block", fake_download_block)
    cache._total_bytes = None
    prefetch._block_locks = {}
    prefetch._urgent_keys = set()
    prefetch._urgent_empty = asyncio.Event()
    prefetch._urgent_empty.set()
    prefetch._work_available = asyncio.Event()
    prefetch._pin = None
    prefetch._priority = None
    prefetch._paused = False
    prefetch._active_tier = None
    prefetch._worker_task = None
    prefetch._worker_download_task = None
    prefetch._worker_download_key = None
    prefetch._visible_channel = None
    prefetch._visible_ids = []
    prefetch.reset_visible_pass()
    prefetch.reset_prewarm_pass()
    return calls


async def run_until_idle():
    while True:
        job = await prefetch.select_worker_job()
        if job is None:
            return
        await prefetch.run_worker_download(*job)
