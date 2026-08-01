import asyncio
from types import SimpleNamespace

import cache
import config
import downloader
import prefetch
import telegram


async def test_prewarm_enumerates_newest_first_and_skips_cached_blocks(
    monkeypatch, tmp_path
):
    first_page = [make_video(msg_id) for msg_id in range(100, 50, -1)]
    last_page = [make_video(50), make_video(49)]
    sizes = {video["id"]: 1 for video in first_page + last_page}
    sizes[100] = 2
    sizes[99] = 3
    calls = install_fakes(monkeypatch, tmp_path, [first_page, last_page], sizes)
    cache.write_block(100, 0, b"x")
    cache.write_block(100, 1, b"x")
    cache.write_block(99, 0, b"x")
    cache.write_block(99, 2, b"x")

    await run_one_pass()

    assert calls["lists"] == [(50, None), (50, 51)]
    assert calls["downloads"] == [(99, 1)] + [
        (msg_id, 0) for msg_id in range(98, 48, -1)
    ]


async def test_prewarm_skips_a_missing_message_and_continues(monkeypatch, tmp_path):
    calls = install_fakes(
        monkeypatch,
        tmp_path,
        [[make_video(3), make_video(2), make_video(1)]],
        {3: 1, 2: None, 1: 1},
    )

    await run_one_pass()

    assert calls["messages"] == [3, 2, 1]
    assert calls["downloads"] == [(3, 0), (1, 0)]


async def test_prewarm_stops_before_video_that_would_cross_cap(monkeypatch, tmp_path):
    calls = install_fakes(
        monkeypatch,
        tmp_path,
        [[make_video(2), make_video(1)]],
        {2: 3, 1: 3},
        max_bytes=5,
    )

    await run_one_pass()

    assert calls["downloads"] == [(2, 0), (2, 1), (2, 2)]
    assert cache.current_total() == 3


async def test_prewarm_rescans_from_newest_after_exhausted_pass(
    monkeypatch, tmp_path
):
    calls = install_fakes(
        monkeypatch, tmp_path, [[make_video(1)]], {1: 1}
    )

    await run_worker_until_second_enumeration(monkeypatch, calls)

    assert calls["lists"][:2] == [(50, None), (50, None)]
    assert calls["sleeps"]


async def test_prewarm_rescans_from_newest_after_cap_terminated_pass(
    monkeypatch, tmp_path
):
    calls = install_fakes(
        monkeypatch,
        tmp_path,
        [[make_video(2), make_video(1)]],
        {2: 3, 1: 3},
        max_bytes=5,
    )

    await run_worker_until_second_enumeration(monkeypatch, calls)

    assert calls["lists"][:2] == [(50, None), (50, None)]
    assert calls["sleeps"]
    assert calls["downloads"] == [(2, 0), (2, 1), (2, 2)]


async def test_none_page_logs_and_next_rescan_reenumerates(
    monkeypatch, tmp_path, capsys
):
    calls = install_fakes(monkeypatch, tmp_path, [None, []], {})

    assert await prefetch.select_prewarm_job() is None
    retry_line = "listing prewarm videos failed; will retry at next rescan"
    assert retry_line in capsys.readouterr().out

    prefetch.reset_prewarm_pass()
    assert await prefetch.select_prewarm_job() is None
    assert calls["lists"] == [(50, None), (50, None)]


async def test_empty_page_ends_pass_silently(monkeypatch, tmp_path, capsys):
    install_fakes(monkeypatch, tmp_path, [[]], {})

    assert await prefetch.select_prewarm_job() is None

    retry_line = "listing prewarm videos failed; will retry at next rescan"
    assert retry_line not in capsys.readouterr().out


async def test_disabled_prewarm_never_downloads_library_but_pin_still_downloads(
    monkeypatch, tmp_path
):
    calls = install_fakes(
        monkeypatch, tmp_path, [[make_video(9)]], {7: 2, 9: 1}
    )
    monkeypatch.setattr(prefetch.config, "PREWARM_ENABLED", False)
    prefetch.note_playhead(7, 0)

    while True:
        job = await prefetch.select_worker_job()
        if job is None:
            break
        await prefetch.run_worker_download(*job)

    assert calls["downloads"] == [(7, 0), (7, 1)]
    assert calls["lists"] == []


# --- helpers ---


def make_video(msg_id):
    return {"id": msg_id}


def make_message(msg_id, size):
    if size is None:
        return None
    return SimpleNamespace(id=msg_id, file=SimpleNamespace(size=size))


def install_fakes(monkeypatch, tmp_path, pages, sizes, max_bytes=10_000):
    calls = {"lists": [], "messages": [], "downloads": [], "sleeps": []}

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
    prefetch._worker_task = None
    prefetch._worker_download_task = None
    prefetch._worker_download_key = None
    prefetch.reset_prewarm_pass()
    return calls


async def run_one_pass():
    while True:
        job = await prefetch.select_prewarm_job()
        if job is None:
            return
        await prefetch.run_worker_download(*job)


async def run_worker_until_second_enumeration(monkeypatch, calls):
    """Run the real worker loop across a faked rescan until it re-enumerates."""
    second_enumeration = asyncio.Event()

    async def fake_sleep_before_rescan():
        calls["sleeps"].append(config.PREWARM_RESCAN_SECONDS)
        prefetch.reset_prewarm_pass()
        await asyncio.sleep(0)

    original_list_videos = telegram.list_videos

    async def observing_list_videos(limit, before_id):
        page = await original_list_videos(limit, before_id)
        if len(calls["lists"]) == 2:
            second_enumeration.set()
        return page

    monkeypatch.setattr(prefetch, "sleep_before_rescan", fake_sleep_before_rescan)
    monkeypatch.setattr(telegram, "list_videos", observing_list_videos)
    worker = asyncio.create_task(prefetch.run_worker())
    try:
        for _ in range(200):
            if second_enumeration.is_set():
                break
            await asyncio.sleep(0)
        assert second_enumeration.is_set()
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
