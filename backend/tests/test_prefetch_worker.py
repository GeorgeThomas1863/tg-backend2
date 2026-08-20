"""Runtime behavior of the serial prefetch worker."""

import asyncio
from types import SimpleNamespace

import cache
import config
import downloader
import prefetch
import telegram
from config import BLOCK_SIZE


async def test_urgent_download_pauses_new_worker_downloads(tmp_path, monkeypatch):
    world = install_world(tmp_path, monkeypatch, [make_msg(1), make_msg(2)])
    urgent = asyncio.create_task(prefetch.get_block("test", world["messages"][2], 0, True))
    await wait_until(lambda: world["calls"] == [(2, 0)])

    prefetch.note_playhead("test", 1, 0)
    await prefetch.start()
    await yield_many()
    assert world["calls"] == [(2, 0)]

    world["gates"][(2, 0, 1)].set()
    await urgent
    await wait_until(lambda: (1, 0) in world["calls"])
    world["gates"][(1, 0, 1)].set()
    await prefetch.stop()


async def test_urgent_during_selection_delays_worker_download(tmp_path, monkeypatch):
    world = install_world(tmp_path, monkeypatch, [make_msg(1), make_msg(2)])
    selection_started = asyncio.Event()
    release_selection = asyncio.Event()

    async def blocking_get_message(msg_id):
        selection_started.set()
        await release_selection.wait()
        return world["messages"].get(msg_id)

    monkeypatch.setattr(telegram, "get_message", blocking_get_message)
    prefetch.note_playhead("test", 1, 0)
    await prefetch.start()
    await selection_started.wait()

    urgent = asyncio.create_task(prefetch.get_block("test", world["messages"][2], 0, True))
    await wait_until(lambda: world["calls"] == [(2, 0)])
    release_selection.set()
    await yield_many()
    assert world["calls"] == [(2, 0)]

    world["gates"][(2, 0, 1)].set()
    await urgent
    await wait_until(lambda: (1, 0) in world["calls"])
    world["gates"][(1, 0, 1)].set()
    await prefetch.stop()


async def test_urgent_get_block_returns_none_on_downloader_exception(
    tmp_path, monkeypatch
):
    world = install_world(
        tmp_path,
        monkeypatch,
        [make_msg(1)],
        outcomes={(1, 0, 1): RuntimeError("broken")},
    )

    assert await prefetch.get_block("test", world["messages"][1], 0, True) is None


async def test_cancelled_urgent_get_block_propagates(tmp_path, monkeypatch):
    world = install_world(tmp_path, monkeypatch, [make_msg(1)])
    task = asyncio.create_task(prefetch.get_block("test", world["messages"][1], 0, True))
    await wait_until(lambda: world["calls"] == [(1, 0)])

    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError did not propagate")


async def test_different_urgent_block_preempts_and_worker_retries(
    tmp_path, monkeypatch
):
    world = install_world(tmp_path, monkeypatch, [make_msg(1), make_msg(2)])
    prefetch.note_playhead("test", 1, 0)
    await prefetch.start()
    await wait_until(lambda: world["calls"] == [(1, 0)])

    urgent = asyncio.create_task(prefetch.get_block("test", world["messages"][2], 0, True))
    await wait_until(lambda: (1, 0, 1) in world["cancelled"])
    await wait_until(lambda: (2, 0) in world["calls"])
    world["gates"][(2, 0, 1)].set()
    await urgent

    await wait_until(lambda: world["calls"].count((1, 0)) == 2)
    world["gates"][(1, 0, 2)].set()
    await wait_until(lambda: cache.has_block("test", 1, 0))
    await prefetch.stop()

    assert world["calls"].count((1, 0)) == 2
    assert cache.read_block("test", 1, 0) == block_bytes(1, 0)


async def test_cancelled_visible_download_requeues_its_block(tmp_path, monkeypatch):
    world = install_world(
        tmp_path, monkeypatch, [make_msg(1, 3 * BLOCK_SIZE), make_msg(2)]
    )
    prefetch._visible_channel = "test"
    prefetch._visible_message = world["messages"][1]
    prefetch._visible_blocks = [2]
    prefetch._active_tier = "visible"
    idx = 1

    download = asyncio.create_task(
        prefetch.run_worker_download("test", world["messages"][1], idx)
    )
    await wait_until(lambda: world["calls"] == [(1, idx)])

    urgent = asyncio.create_task(
        prefetch.get_block("test", world["messages"][2], 0, True)
    )
    await wait_until(lambda: (1, idx, 1) in world["cancelled"])
    await download

    assert prefetch._visible_blocks == [idx, 2]

    world["gates"][(2, 0, 1)].set()
    await urgent


async def test_visible_reset_during_cancel_does_not_reinsert_stale_block(
    tmp_path, monkeypatch
):
    world = install_world(
        tmp_path, monkeypatch, [make_msg(1, 3 * BLOCK_SIZE), make_msg(2)]
    )
    prefetch._visible_channel = "test"
    prefetch._visible_message = world["messages"][1]
    prefetch._visible_blocks = [2]
    prefetch._active_tier = "visible"
    idx = 1

    download = asyncio.create_task(
        prefetch.run_worker_download("test", world["messages"][1], idx)
    )
    await wait_until(lambda: world["calls"] == [(1, idx)])

    # A concurrent set_visible() resets the walk without cancelling the
    # in-flight download for the old video.
    prefetch.set_visible("test", [])

    urgent = asyncio.create_task(
        prefetch.get_block("test", world["messages"][2], 0, True)
    )
    await wait_until(lambda: (1, idx, 1) in world["cancelled"])
    await download

    assert prefetch._visible_message is None
    assert prefetch._visible_blocks == []

    world["gates"][(2, 0, 1)].set()
    await urgent


async def test_stop_mid_visible_download_propagates_cancel_without_requeue(
    tmp_path, monkeypatch
):
    world = install_world(
        tmp_path, monkeypatch, [make_msg(1, 3 * BLOCK_SIZE)]
    )
    prefetch._visible_channel = "test"
    prefetch._visible_message = world["messages"][1]
    prefetch._visible_blocks = [2]
    prefetch._active_tier = "visible"
    idx = 1

    # Wrapping run_worker_download in its own task and cancelling that task
    # (rather than only the inner _worker_download_task) mirrors what
    # stop() does to _worker_task when it is awaiting run_worker_download.
    download = asyncio.create_task(
        prefetch.run_worker_download("test", world["messages"][1], idx)
    )
    await wait_until(lambda: world["calls"] == [(1, idx)])

    download.cancel()

    try:
        await download
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError did not propagate")

    assert download.cancelled()
    assert prefetch._visible_blocks == [2]


async def test_same_urgent_block_shares_worker_download(tmp_path, monkeypatch):
    world = install_world(tmp_path, monkeypatch, [make_msg(1)])
    prefetch.note_playhead("test", 1, 0)
    await prefetch.start()
    await wait_until(lambda: world["calls"] == [(1, 0)])

    urgent = asyncio.create_task(prefetch.get_block("test", world["messages"][1], 0, True))
    await yield_many()
    assert world["calls"] == [(1, 0)]
    assert world["cancelled"] == []

    world["gates"][(1, 0, 1)].set()
    assert await urgent == block_bytes(1, 0)
    await wait_until(lambda: cache.has_block("test", 1, 0))
    await prefetch.stop()

    assert world["calls"] == [(1, 0)]


async def test_worker_survives_none_and_exception_then_moves_on(
    tmp_path, monkeypatch, capsys
):
    outcomes = {(1, 0, 1): None, (1, 1, 1): RuntimeError("broken")}
    world = install_world(
        tmp_path, monkeypatch, [make_msg(1, 3 * BLOCK_SIZE)], outcomes=outcomes
    )
    prefetch.note_playhead("test", 1, 0)
    await prefetch.start()

    await wait_until(lambda: (1, 2) in world["calls"])
    world["gates"][(1, 2, 1)].set()
    await wait_until(lambda: cache.has_block("test", 1, 2))
    assert prefetch._worker_task is not None
    assert not prefetch._worker_task.done()
    await prefetch.stop()

    output = capsys.readouterr().out
    assert world["calls"][:3] == [(1, 0), (1, 1), (1, 2)]
    assert "PREFETCH ERROR worker block 1/0" in output
    assert "PREFETCH ERROR worker block 1/1" in output


async def test_stop_cancels_worker_and_inflight_download(tmp_path, monkeypatch):
    world = install_world(tmp_path, monkeypatch, [make_msg(1)])
    prefetch.note_playhead("test", 1, 0)
    await prefetch.start()
    worker = prefetch._worker_task
    await wait_until(lambda: world["calls"] == [(1, 0)])
    download = prefetch._worker_download_task

    await prefetch.stop()

    assert worker.done()
    assert download.done()
    assert download.cancelled()
    assert prefetch._worker_task is None
    assert prefetch._worker_download_task is None


async def test_disabled_prewarm_still_runs_pin_tier(tmp_path, monkeypatch):
    world = install_world(tmp_path, monkeypatch, [make_msg(1)])
    monkeypatch.setattr(config, "PREWARM_ENABLED", False)
    prefetch.note_playhead("test", 1, 0)

    await prefetch.start()
    await wait_until(lambda: world["calls"] == [(1, 0)])
    world["gates"][(1, 0, 1)].set()
    await wait_until(lambda: cache.has_block("test", 1, 0))
    await prefetch.stop()

    assert world["calls"] == [(1, 0)]


async def test_paused_worker_selects_no_job(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, [make_msg(1)])
    monkeypatch.setattr(config, "PREWARM_ENABLED", True)
    prefetch.note_playhead("test", 1, 0)
    prefetch.set_paused(True)

    assert await prefetch.select_worker_job() is None


async def test_pausing_cancels_inflight_worker_download(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, [])
    download = asyncio.create_task(asyncio.Event().wait())
    prefetch._worker_download_task = download

    prefetch.set_paused(True)
    await yield_many()

    assert download.cancelled()


async def test_resuming_wakes_parked_worker(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, [])
    prefetch.set_paused(True)

    prefetch.set_paused(False)

    assert prefetch._work_available.is_set()


async def test_setting_priority_wakes_parked_worker(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, [])

    prefetch.set_priority("test", 1)

    assert prefetch._work_available.is_set()


async def test_status_reports_idle_active_tiers_and_paused(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, [])
    pin_job = ("test", make_msg(1), 0)
    prewarm_job = ("test", make_msg(2), 0)

    async def select_pin():
        return pin_job

    async def select_no_pin():
        return None

    async def select_prewarm():
        return prewarm_job

    assert prefetch.status() == {"paused": False, "active": None}

    monkeypatch.setattr(prefetch, "select_pin_job", select_pin)
    assert await prefetch.select_worker_job() == pin_job
    prefetch._worker_download_key = ("test", 1, 0)
    assert prefetch.status() == {
        "paused": False,
        "active": {"msg_id": 1, "tier": "pin"},
    }

    prefetch._worker_download_key = None
    monkeypatch.setattr(prefetch, "select_pin_job", select_no_pin)
    monkeypatch.setattr(prefetch, "select_prewarm_job", select_prewarm)
    monkeypatch.setattr(config, "PREWARM_ENABLED", True)
    assert await prefetch.select_worker_job() == prewarm_job
    prefetch._worker_download_key = ("test", 2, 0)
    assert prefetch.status() == {
        "paused": False,
        "active": {"msg_id": 2, "tier": "prewarm"},
    }

    prefetch.set_paused(True)
    assert prefetch.status() == {
        "paused": True,
        "active": {"msg_id": 2, "tier": "prewarm"},
    }


# --- helpers ---


def install_world(tmp_path, monkeypatch, messages, outcomes=None):
    calls = []
    cancelled = []
    gates = {}
    attempts = {}
    messages_by_id = {msg.id: msg for msg in messages}
    outcomes = outcomes or {}

    async def fake_download_block(msg, idx):
        key = (msg.id, idx)
        attempts[key] = attempts.get(key, 0) + 1
        attempt_key = (*key, attempts[key])
        calls.append(key)
        outcome = outcomes.get(attempt_key, block_bytes(*key))
        if outcome is None:
            return None
        if isinstance(outcome, Exception):
            raise outcome
        gate = gates.setdefault(attempt_key, asyncio.Event())
        try:
            await gate.wait()
        except asyncio.CancelledError:
            cancelled.append(attempt_key)
            raise
        return outcome

    async def fake_get_message(msg_id):
        return messages_by_id.get(msg_id)

    async def fake_list_videos(limit, before_id):
        return []

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache, "MAX_BYTES", 10**12)
    monkeypatch.setattr(cache, "_total_bytes", None)
    monkeypatch.setattr(config, "PREWARM_ENABLED", False)
    monkeypatch.setattr(config, "PREWARM_RESCAN_SECONDS", 3600)
    monkeypatch.setattr(downloader, "download_block", fake_download_block)
    monkeypatch.setattr(telegram, "get_message", fake_get_message)
    monkeypatch.setattr(telegram, "list_videos", fake_list_videos)
    reset_prefetch_state(monkeypatch)
    return {
        "calls": calls,
        "cancelled": cancelled,
        "gates": gates,
        "messages": messages_by_id,
    }


def reset_prefetch_state(monkeypatch):
    prefetch._block_locks.clear()
    prefetch._urgent_keys.clear()
    prefetch._urgent_empty = asyncio.Event()
    prefetch._urgent_empty.set()
    prefetch._work_available = asyncio.Event()
    prefetch._pin = None
    prefetch._priority = None
    prefetch._worker_task = None
    prefetch._worker_download_task = None
    prefetch._worker_download_key = None
    monkeypatch.setattr(prefetch, "_paused", False)
    monkeypatch.setattr(prefetch, "_active_tier", None)
    prefetch._failed_keys.clear()
    prefetch._logged_oversized_pins.clear()
    prefetch.reset_prewarm_pass()


def make_msg(msg_id, file_size=BLOCK_SIZE):
    return SimpleNamespace(
        id=msg_id,
        file=SimpleNamespace(size=file_size),
        media=object(),
    )


def block_bytes(msg_id, idx):
    return f"block-{msg_id}-{idx}".encode()


async def wait_until(predicate, iterations=100):
    for _ in range(iterations):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


async def yield_many(iterations=20):
    for _ in range(iterations):
        await asyncio.sleep(0)
