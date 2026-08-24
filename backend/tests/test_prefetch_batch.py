"""Batch download tier: enumerate a filter selection and cache it (todo item 5)."""

import asyncio
from types import SimpleNamespace

import cache
import categories
import channels
import config
import downloader
import prefetch
import telegram
from config import BLOCK_SIZE


# --- tier selection / ranking ---


async def test_visible_outranks_batch(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    visible_msg = make_msg(1, BLOCK_SIZE)
    batch_msg = make_msg(2, BLOCK_SIZE)
    messages = {1: visible_msg, 2: batch_msg}

    async def get_message(msg_id):
        return messages[msg_id]

    async def list_videos(limit, before_id=None, cat_start=None, cat_end=None):
        return [{"id": 2}]

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(telegram, "list_videos", list_videos)
    prefetch.set_batch("test", None, None, 1)
    prefetch.set_visible("test", [1])

    job = await prefetch.select_worker_job()

    assert job == ("test", visible_msg, 0)
    assert prefetch._active_tier == "visible"


async def test_batch_outranks_prewarm(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    batch_msg = make_msg(2, BLOCK_SIZE)

    async def get_message(msg_id):
        return batch_msg

    async def list_videos(limit, before_id=None, cat_start=None, cat_end=None):
        return [{"id": 2}]

    async def select_prewarm_job(slot=0):
        raise AssertionError("prewarm must not run while batch has work queued")

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(telegram, "list_videos", list_videos)
    monkeypatch.setattr(prefetch, "select_prewarm_job", select_prewarm_job)
    monkeypatch.setattr(config, "PREWARM_ENABLED", True)
    prefetch.set_batch("test", None, None, 1)

    job = await prefetch.select_worker_job()

    assert job == ("test", batch_msg, 0)
    assert prefetch._active_tier == "batch"


async def test_batch_skips_video_exceeding_budget_and_continues(
    tmp_path, monkeypatch
):
    install_world(tmp_path, monkeypatch)
    too_big_msg = make_msg(1, BLOCK_SIZE)
    fits_msg = make_msg(2, BLOCK_SIZE // 2)
    messages = {1: too_big_msg, 2: fits_msg}
    monkeypatch.setattr(cache, "MAX_BYTES", BLOCK_SIZE // 2)

    async def get_message(msg_id):
        return messages[msg_id]

    async def list_videos(limit, before_id=None, cat_start=None, cat_end=None):
        if before_id is None:
            return [{"id": 1}, {"id": 2}]
        return []

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(telegram, "list_videos", list_videos)
    prefetch.set_batch("test", None, None, 2)

    job = await prefetch.select_batch_job()

    assert job == ("test", fits_msg, 0)


# --- POST /api/prefetch/batch ---


def test_batch_requires_auth(client):
    resp = client.post("/api/prefetch/batch", json={"category": None})
    assert resp.status_code == 401


def test_batch_null_category_targets_all_videos(authed_client, monkeypatch):
    calls = []

    async def fake_list_videos_with_total(**kwargs):
        calls.append(kwargs)
        return [], 250

    monkeypatch.setattr(
        telegram, "list_videos_with_total", fake_list_videos_with_total
    )

    resp = authed_client.post("/api/prefetch/batch", json={"category": None})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "success": True,
        "message": "Downloading 250 videos to cache",
        "queued": 250,
    }
    assert calls == [{"limit": 1, "cat_start": None, "cat_end": None}]
    assert prefetch.status()["batch"] == {
        "active": True,
        "total": 250,
        "remaining": 250,
    }


def test_batch_category_total_uses_category_count_not_channel_wide(
    authed_client, monkeypatch
):
    """A category-scoped batch must report the CATEGORY's total, not the
    whole channel's -- Telethon's SearchRequest (used for
    filter=InputMessagesFilterVideo) hardcodes min_id=max_id=0 server-side,
    so cat_start/cat_end never reach Telegram and its count is channel-wide.
    If the handler ever asks Telegram for that count again, this fails loud
    instead of silently reporting the wrong number.
    """
    install_category_world(monkeypatch, count=87, counts_exact=True)

    async def forbidden_list_videos_with_total(**kwargs):
        raise AssertionError(
            "category-scoped batch must not use Telegram's count -- it is "
            "channel-wide, not category-scoped (see docstring above)"
        )

    monkeypatch.setattr(
        telegram, "list_videos_with_total", forbidden_list_videos_with_total
    )

    resp = authed_client.post("/api/prefetch/batch", json={"category": "old"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "success": True,
        "message": "Downloading 87 videos to cache",
        "queued": 87,
    }
    assert "capped" not in body["message"]
    assert prefetch.status()["batch"] == {
        "active": True,
        "total": 87,
        "remaining": 87,
    }


def test_batch_category_over_cap_reports_category_total(authed_client, monkeypatch):
    install_category_world(monkeypatch, count=6000, counts_exact=True)

    resp = authed_client.post("/api/prefetch/batch", json={"category": "old"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == prefetch.BATCH_ENQUEUE_CAP
    assert "6000" in body["message"]
    assert str(prefetch.BATCH_ENQUEUE_CAP) in body["message"]


def test_batch_category_inexact_count_is_flagged_approximate(
    authed_client, monkeypatch
):
    install_category_world(monkeypatch, count=87, counts_exact=False)

    resp = authed_client.post("/api/prefetch/batch", json={"category": "old"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == 87
    assert "approximat" in body["message"].lower()


def test_batch_unknown_category_returns_404(authed_client, monkeypatch):
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)

    async def fake_ensure_fresh():
        return None

    monkeypatch.setattr(categories, "ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(categories, "resolve", lambda key: None)

    resp = authed_client.post("/api/prefetch/batch", json={"category": "missing"})

    assert resp.status_code == 404
    assert resp.json() == {"detail": "unknown category"}


def test_batch_wrong_channel_returns_400(authed_client, monkeypatch):
    monkeypatch.setattr(channels, "active_key", lambda: "other")

    resp = authed_client.post("/api/prefetch/batch", json={"category": "old"})

    assert resp.status_code == 400
    assert resp.json() == {"detail": "categories unavailable for this channel"}


def test_batch_enqueue_cap_truncates_and_says_so(authed_client, monkeypatch):
    async def fake_list_videos_with_total(**kwargs):
        return [], 999_999

    monkeypatch.setattr(
        telegram, "list_videos_with_total", fake_list_videos_with_total
    )

    resp = authed_client.post("/api/prefetch/batch", json={"category": None})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["queued"] == prefetch.BATCH_ENQUEUE_CAP
    assert str(prefetch.BATCH_ENQUEUE_CAP) in body["message"]
    assert "999999" in body["message"] or "999,999" in body["message"]


def test_batch_without_active_channel_fails(authed_client, monkeypatch):
    monkeypatch.setattr(channels, "_active_channel", None)

    resp = authed_client.post("/api/prefetch/batch", json={"category": None})

    assert resp.status_code == 200
    assert resp.json() == {
        "success": False,
        "message": "No active channel",
        "queued": 0,
    }


def test_batch_starting_new_pass_replaces_the_reported_total(authed_client, monkeypatch):
    """Route-level bookkeeping only: the second POST's total wins. The two
    tests below cover what replacement does to work already in flight."""
    calls = []

    async def fake_list_videos_with_total(**kwargs):
        calls.append(kwargs)
        return [], len(calls) * 10

    monkeypatch.setattr(
        telegram, "list_videos_with_total", fake_list_videos_with_total
    )

    first = authed_client.post("/api/prefetch/batch", json={"category": None})
    second = authed_client.post("/api/prefetch/batch", json={"category": None})

    assert first.json()["queued"] == 10
    assert second.json()["queued"] == 20
    assert prefetch.status()["batch"]["total"] == 20


async def test_set_batch_cancels_inflight_batch_download(tmp_path, monkeypatch):
    """A replacement pass must stop the superseded pass's in-flight block, the
    way clear_batch does. Otherwise the abandoned range keeps downloading and
    holds a worker slot the new range is waiting on. Mirrors
    test_clear_batch_cancels_inflight_download below, but via set_batch."""
    install_world(tmp_path, monkeypatch)
    batch_msg = make_msg(9, BLOCK_SIZE)
    started = asyncio.Event()
    release = asyncio.Event()

    async def get_message(msg_id):
        return batch_msg

    async def list_videos(limit, before_id=None, cat_start=None, cat_end=None):
        if cat_start is not None:
            return []  # the replacement range is empty, so only one pass has work
        if before_id is None:
            return [{"id": 9}]
        return []

    async def slow_download(msg, idx):
        started.set()
        await release.wait()
        return b"x"

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(telegram, "list_videos", list_videos)
    monkeypatch.setattr(downloader, "download_block", slow_download)
    prefetch.set_batch("test", None, None, 1)

    worker = asyncio.create_task(prefetch.run_worker())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert prefetch._active_tiers[0] == "batch"
        task = prefetch._worker_download_tasks[0]

        prefetch.set_batch("test", 500, 900, 3)

        for _ in range(200):
            if task.done():
                break
            await asyncio.sleep(0)
        assert task.cancelled()
        # The cancelled block belonged to the old range: it must not be
        # requeued into the replacement pass.
        assert prefetch._batch_blocks_by_id.get(9) is None
    finally:
        release.set()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


async def test_replacement_batch_discards_a_page_the_old_pass_was_listing(
    tmp_path, monkeypatch
):
    """set_batch during an in-flight listing must make that listing's ids drop
    on the floor. They belong to the abandoned range, and installing them
    would page the replacement pass through the wrong category and cursor."""
    install_world(tmp_path, monkeypatch)
    listing_started = asyncio.Event()
    release_listing = asyncio.Event()

    async def slow_list_videos(limit, before_id=None, cat_start=None, cat_end=None):
        listing_started.set()
        await release_listing.wait()
        return [{"id": 41}, {"id": 42}]

    monkeypatch.setattr(telegram, "list_videos", slow_list_videos)
    prefetch.set_batch("test", 1, 100, 2)

    page_task = asyncio.create_task(prefetch.load_next_batch_page())
    await asyncio.wait_for(listing_started.wait(), timeout=1)

    prefetch.set_batch("test", 500, 900, 7)
    release_listing.set()

    assert await asyncio.wait_for(page_task, timeout=1) is False
    assert prefetch._batch_page == []
    assert prefetch._batch_cursor is None
    assert prefetch._batch_is_last_page is False


# --- DELETE /api/prefetch/batch ---


def test_delete_batch_requires_auth(client):
    resp = client.delete("/api/prefetch/batch")
    assert resp.status_code == 401


def test_delete_batch_stops_the_pass(authed_client, monkeypatch):
    async def fake_list_videos_with_total(**kwargs):
        return [], 40

    monkeypatch.setattr(
        telegram, "list_videos_with_total", fake_list_videos_with_total
    )
    authed_client.post("/api/prefetch/batch", json={"category": None})
    assert prefetch.status()["batch"]["active"] is True

    resp = authed_client.delete("/api/prefetch/batch")

    assert resp.status_code == 200
    assert resp.json() == {"success": True, "message": "Batch download cancelled"}
    assert prefetch.status()["batch"] == {
        "active": False,
        "total": 0,
        "remaining": 0,
    }


async def test_clear_batch_cancels_inflight_download(tmp_path, monkeypatch):
    """clear_batch() (called by DELETE /api/prefetch/batch) must stop a block
    already dispatched to a worker slot, not just reset bookkeeping --
    otherwise "Batch download cancelled" is a lie and the download keeps
    running. Mirrors test_set_visible_cancels_inflight_prewarm_download in
    test_prefetch_visible.py, but for the batch tier and via clear_batch
    directly (the DELETE route is a one-line delegation to it, already
    covered by test_delete_batch_stops_the_pass above)."""
    install_world(tmp_path, monkeypatch)
    batch_msg = make_msg(9, BLOCK_SIZE)
    started = asyncio.Event()
    release = asyncio.Event()

    async def get_message(msg_id):
        return batch_msg

    async def list_videos(limit, before_id=None, cat_start=None, cat_end=None):
        if before_id is None:
            return [{"id": 9}]
        return []

    async def slow_download(msg, idx):
        started.set()
        await release.wait()
        return b"x"

    monkeypatch.setattr(telegram, "get_message", get_message)
    monkeypatch.setattr(telegram, "list_videos", list_videos)
    monkeypatch.setattr(downloader, "download_block", slow_download)
    prefetch.set_batch("test", None, None, 1)

    worker = asyncio.create_task(prefetch.run_worker())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert prefetch._active_tiers[0] == "batch"
        task = prefetch._worker_download_tasks[0]

        prefetch.clear_batch()

        for _ in range(200):
            if task.done():
                break
            await asyncio.sleep(0)
        assert task.cancelled()
    finally:
        release.set()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


# --- helpers ---


def install_category_world(monkeypatch, count, counts_exact):
    """Route a batch request into categories.py's per-category count path
    instead of Telegram, with `count` and `counts_exact` under our control."""
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)

    async def fake_ensure_fresh():
        return None

    async def fake_get_categories():
        return {
            "counts_exact": counts_exact,
            "categories": [{"key": "old", "count": count, "subs": []}],
        }

    monkeypatch.setattr(categories, "ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(categories, "resolve", lambda key: (3, 90))
    monkeypatch.setattr(categories, "get_categories", fake_get_categories)


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
    prefetch._visible_channel = None
    prefetch._visible_ids = []
    prefetch.reset_prewarm_pass()
    prefetch.clear_batch()
    prefetch.initialize_slots()


def make_msg(msg_id, file_size):
    return SimpleNamespace(
        id=msg_id,
        file=SimpleNamespace(size=file_size),
        media=object(),
    )
