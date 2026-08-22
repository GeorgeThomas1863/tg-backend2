"""
Serial cache preloader and shared block acquisition.

Urgent streaming misses preempt different background blocks. Otherwise one
worker fills the pinned video, then completes a one-slot priority video
front-to-back, then downloads the videos currently on screen in display
order, then prewarms the library newest-first without crossing the cache
cap. All acquisition is cache-first and deduplicated by key.
"""

import asyncio
import traceback

import cache
import channels
import config
import downloader
import telegram

_block_locks: dict = {}
_urgent_keys: set = set()
_urgent_empty = asyncio.Event()
_urgent_empty.set()
_work_available = asyncio.Event()
_select_lock = asyncio.Lock()

_pin: tuple[str, int, int] | None = None
_priority: tuple[str, int] | None = None
_worker_task = None
_worker_tasks: list[asyncio.Task] = []
_worker_download_task = None
_worker_download_key = None
_worker_download_tasks: list[asyncio.Task | None] = []
_worker_download_keys: list[tuple | None] = []
_paused: bool = False
_active_tier: str | None = None
_active_tiers: list[str | None] = []
_slot_msg_ids: list[int | None] = []
_failed_keys: set[tuple[int, int]] = set()
_logged_oversized_pins: set[int] = set()

_prewarm_page = []
_prewarm_page_index = 0
_prewarm_cursor = None
_prewarm_is_last_page = False
_prewarm_blocks = []
_prewarm_message = None
_prewarm_blocks_by_id: dict[int, list[int]] = {}
_prewarm_messages: dict[int, object] = {}

_visible_channel: str | None = None
_visible_ids: list[int] = []
_visible_index = 0
_visible_pass_id = 0
_visible_blocks: list[int] = []
_visible_message = None
_visible_bytes_planned = 0
_visible_budget_ids: set[int] = set()
_visible_blocks_by_id: dict[int, list[int]] = {}
_visible_messages: dict[int, object] = {}


# --- public lifecycle + acquisition ---


async def start() -> None:
    """Start every configured background preload slot."""
    global _worker_task, _worker_tasks
    if _worker_tasks and any(not task.done() for task in _worker_tasks):
        return
    reset_prewarm_pass()
    initialize_slots()
    _worker_tasks = []
    for slot in range(config.PREFETCH_SLOTS):
        _worker_tasks.append(asyncio.create_task(run_worker(slot)))
    _worker_task = _worker_tasks[0]


async def stop() -> None:
    """Cancel the worker and wait until its download has cleaned up."""
    global _worker_task, _worker_tasks, _worker_download_task
    tasks = list(_worker_tasks)
    if not tasks and _worker_task is not None:
        tasks = [_worker_task]
    download_tasks = [task for task in _worker_download_tasks if task is not None]
    if not download_tasks and _worker_download_task is not None:
        download_tasks = [_worker_download_task]
    _worker_task = None
    _worker_tasks = []
    for task in tasks:
        task.cancel()
    for task in download_tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            report_error("stopping worker")
    for download_task in download_tasks:
        try:
            await download_task
        except asyncio.CancelledError:
            pass
        except Exception:
            report_error("stopping worker download")
    _worker_download_task = None
    clear_runtime_state()


def initialize_slots() -> None:
    global _worker_download_tasks, _worker_download_keys, _active_tiers, _slot_msg_ids
    _worker_download_tasks = [None] * config.PREFETCH_SLOTS
    _worker_download_keys = [None] * config.PREFETCH_SLOTS
    _active_tiers = [None] * config.PREFETCH_SLOTS
    _slot_msg_ids = [None] * config.PREFETCH_SLOTS


def clear_runtime_state() -> None:
    global _pin, _priority, _worker_download_key, _active_tier, _visible_channel, _visible_ids
    _pin = None
    _priority = None
    _worker_download_key = None
    _active_tier = None
    _visible_channel = None
    _visible_ids = []
    _urgent_keys.clear()
    _urgent_empty.set()
    _work_available.clear()
    _block_locks.clear()
    _failed_keys.clear()
    _logged_oversized_pins.clear()
    initialize_slots()
    reset_visible_pass()
    reset_prewarm_pass()


def set_paused(paused: bool) -> None:
    """Pause or resume background preload work."""
    global _paused
    _paused = paused
    if paused:
        for task in active_download_tasks():
            task.cancel()
        return
    _work_available.set()


def status() -> dict:
    """Report background preload state."""
    active_slots = []
    for slot, key in enumerate(_worker_download_keys):
        if key is None:
            continue
        active_slots.append({"msg_id": key[1], "tier": _active_tiers[slot]})
    if not active_slots and _worker_download_key is not None:
        active_slots.append({"msg_id": _worker_download_key[1], "tier": _active_tier})
    active = active_slots[0] if active_slots else None
    return {"paused": _paused, "active": active, "active_slots": active_slots}


def active_download_tasks() -> list[asyncio.Task]:
    tasks = []
    for task in _worker_download_tasks:
        if task is not None and not task.done():
            tasks.append(task)
    if not tasks and _worker_download_task and not _worker_download_task.done():
        tasks.append(_worker_download_task)
    return tasks


def note_playhead(channel_key: str, msg_id: int, block_idx: int) -> None:
    """Pin a video and record its most recently served block."""
    global _pin
    _pin = (channel_key, msg_id, block_idx)
    _work_available.set()


def set_priority(channel_key: str, msg_id: int) -> None:
    """Set the one-slot priority job and wake the worker for it."""
    global _priority
    _priority = (channel_key, msg_id)
    _work_available.set()


def clear_priority() -> None:
    global _priority
    _priority = None


def set_visible(channel_key: str, msg_ids: list[int]) -> None:
    """Replace the on-screen video list and wake the worker for it."""
    global _visible_channel, _visible_ids
    _visible_channel = channel_key
    _visible_ids = list(dict.fromkeys(msg_ids))
    reset_visible_pass()
    cancel_active_prewarm_download()
    _work_available.set()


def cancel_active_prewarm_download() -> None:
    """Stop an in-flight prewarm block so visible work starts immediately."""
    for slot, tier in enumerate(_active_tiers):
        if tier != "prewarm":
            continue
        task = _worker_download_tasks[slot]
        if task is not None and not task.done():
            task.cancel()
    if not _active_tiers and _active_tier == "prewarm":
        if _worker_download_task and not _worker_download_task.done():
            _worker_download_task.cancel()


async def get_block(channel_key: str, msg, idx: int, urgent: bool) -> bytes | None:
    """Read or download one block; concurrent callers share one fetch."""
    key = (channel_key, msg.id, idx)
    lock = _block_locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            cached = cache.read_block(channel_key, msg.id, idx)
            if cached is not None:
                return cached
            if urgent:
                announce_urgent(key)
            try:
                return await download_and_cache_block(channel_key, msg, idx)
            except asyncio.CancelledError:
                raise
            except Exception:
                report_error(f"downloading block {msg.id}/{idx}")
                return None
            finally:
                if urgent:
                    clear_urgent(key)
    finally:
        prune_locks()


async def download_and_cache_block(channel_key: str, msg, idx: int) -> bytes | None:
    data = await downloader.download_block(msg, idx)
    if data is None:
        return None
    cache.write_block(channel_key, msg.id, idx, data)
    return data


# --- urgent preemption ---


def announce_urgent(key: tuple[int, int]) -> None:
    _urgent_keys.add(key)
    _urgent_empty.clear()
    for slot, worker_key in enumerate(_worker_download_keys):
        if worker_key == key:
            continue
        task = _worker_download_tasks[slot]
        if task is not None and not task.done():
            task.cancel()
    if not _worker_download_keys and _worker_download_key != key:
        if _worker_download_task and not _worker_download_task.done():
            _worker_download_task.cancel()


def clear_urgent(key: tuple[int, int]) -> None:
    _urgent_keys.discard(key)
    if not _urgent_keys:
        _urgent_empty.set()


def prune_locks() -> None:
    # Unlocked entries can be dropped; a rare double-download after a drop
    # is harmless (block writes are atomic) — correctness never depends on it.
    if len(_block_locks) < 8192:
        return
    for key in list(_block_locks):
        if not _block_locks[key].locked():
            del _block_locks[key]


# --- serial worker ---


async def run_worker(slot: int = 0) -> None:
    """Select and run one background block forever."""
    while True:
        try:
            await _urgent_empty.wait()
            job = await select_worker_job(slot)
            if job is None:
                await sleep_before_rescan()
                continue
            await run_worker_download(*job, slot=slot)
        except asyncio.CancelledError:
            raise
        except Exception:
            report_error("worker iteration")


async def run_worker_download(channel_key: str, msg, idx: int, slot: int = 0) -> None:
    global _worker_download_task, _worker_download_key, _active_tier
    if not _urgent_empty.is_set():
        return
    # No await before task + key publication: check-then-publish stays atomic.
    ensure_slot(slot)
    _worker_download_keys[slot] = (channel_key, msg.id, idx)
    _worker_download_tasks[slot] = asyncio.create_task(
        get_block(channel_key, msg, idx, urgent=False)
    )
    if slot == 0:
        _worker_download_key = _worker_download_keys[slot]
        _worker_download_task = _worker_download_tasks[slot]
        _active_tier = _active_tiers[slot]
    try:
        data = await _worker_download_tasks[slot]
        if data is None:
            raise RuntimeError("background block download returned no data")
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
        if _active_tiers[slot] == "visible" and visible_pass_still_on(channel_key, msg.id):
            _visible_blocks_by_id.setdefault(msg.id, []).insert(0, idx)
    except Exception:
        _failed_keys.add((msg.id, idx))
        report_error(f"worker block {msg.id}/{idx}")
    finally:
        _worker_download_tasks[slot] = None
        _worker_download_keys[slot] = None
        if slot == 0:
            _worker_download_task = None
            _worker_download_key = None
            _active_tier = None


def ensure_slot(slot: int) -> None:
    if slot < len(_worker_download_tasks):
        return
    initialize_slots()


async def select_worker_job(slot: int = 0):
    async with _select_lock:
        return await select_and_reserve_worker_job(slot)


async def select_and_reserve_worker_job(slot: int = 0):
    if _paused:
        return None
    ensure_slot(slot)
    pin_job = await select_pin_job(slot)
    if pin_job is not None:
        return reserve_slot_job(slot, "pin", pin_job)
    priority_job = await select_priority_job(slot)
    if priority_job is not None:
        return reserve_slot_job(slot, "priority", priority_job)
    visible_job = await select_visible_job(slot)
    if visible_job is not None:
        return reserve_slot_job(slot, "visible", visible_job)
    if not config.PREWARM_ENABLED:
        return None
    prewarm_job = await select_prewarm_job(slot)
    if prewarm_job is not None:
        return reserve_slot_job(slot, "prewarm", prewarm_job)
    release_slot(slot)
    return None


def reserve_slot_job(slot: int, tier: str, job):
    global _active_tier
    _active_tiers[slot] = tier
    _slot_msg_ids[slot] = job[1].id
    if slot == 0:
        _active_tier = tier
    return job


def release_slot(slot: int) -> None:
    _active_tiers[slot] = None
    _slot_msg_ids[slot] = None


def msg_busy_in_other_slot(msg_id: int, slot: int) -> bool:
    for other_slot, active_msg_id in enumerate(_slot_msg_ids):
        if other_slot != slot and active_msg_id == msg_id:
            return True
    return False


async def select_pin_job(slot: int = 0):
    global _pin
    while _pin is not None:
        channel_key, msg_id, _ = _pin
        if msg_busy_in_other_slot(msg_id, slot):
            return None
        if channels.active_key() != channel_key:
            _pin = None
            return None
        try:
            msg = await telegram.get_message(msg_id)
        except Exception:
            report_error(f"resolving pinned video {msg_id}")
            return None
        if _pin is None:
            return None
        if _pin[0] != channel_key or _pin[1] != msg_id:
            continue
        if msg_busy_in_other_slot(msg_id, slot):
            return None
        if not msg or not msg.file:
            return None
        if msg.file.size > cache.MAX_BYTES:
            if msg.id not in _logged_oversized_pins:
                print(
                    f"PREFETCH pinned video {msg.id} exceeds the cache cap; "
                    "it will stream without tier-1 completion"
                )
                _logged_oversized_pins.add(msg.id)
            return None
        playhead = _pin[2]
        idx = select_pin_block(channel_key, msg_id, playhead, msg.file.size)
        if idx is None:
            return None
        return channel_key, msg, idx
    return None


# --- priority (one-slot, jump the queue) ---


async def select_priority_job(slot: int = 0):
    while _priority is not None:
        channel_key, msg_id = _priority
        if msg_busy_in_other_slot(msg_id, slot):
            return None
        if channels.active_key() != channel_key:
            clear_priority()
            return None
        try:
            msg = await telegram.get_message(msg_id)
        except Exception:
            report_error(f"resolving priority video {msg_id}")
            return None
        if _priority is None:
            return None
        if _priority != (channel_key, msg_id):
            continue
        if msg_busy_in_other_slot(msg_id, slot):
            return None
        if not msg or not msg.file:
            clear_priority()
            return None
        if msg.file.size > cache.MAX_BYTES:
            clear_priority()
            continue
        idx = select_priority_block(channel_key, msg_id, msg.file.size)
        if idx is None:
            clear_priority()
            continue
        return channel_key, msg, idx
    return None


def select_priority_block(channel_key: str, msg_id: int, file_size: int) -> int | None:
    """Choose the first missing block from the start of the file."""
    blocks, _ = build_uncached_blocks(channel_key, msg_id, file_size)
    return blocks[0] if blocks else None


# --- visible pass ---


async def select_visible_job(slot: int = 0):
    channel_key = active_visible_channel()
    if channel_key is None:
        return None
    current_id = _slot_msg_ids[slot] if slot < len(_slot_msg_ids) else None
    current_job = take_visible_video_block(channel_key, current_id)
    if current_job is not None:
        return current_job
    replanned_job = await replan_current_visible_video(channel_key, current_id, slot)
    if replanned_job is not None:
        return replanned_job
    queued_job = take_queued_visible_job(channel_key, slot)
    if queued_job is not None:
        return queued_job
    while True:
        video = await find_next_visible_video(slot)
        if video is None:
            return None
        msg, blocks = video
        _visible_messages[msg.id] = msg
        _visible_blocks_by_id[msg.id] = blocks
        job = take_visible_video_block(channel_key, msg.id)
        if job is not None:
            return job


async def replan_current_visible_video(
    channel_key: str, msg_id: int | None, slot: int
):
    if msg_id is None or msg_id not in _visible_ids:
        return None
    if msg_id in _visible_blocks_by_id:
        return None
    msg = await resolve_visible_message(msg_id)
    if not visible_slot_still_needs_plan(channel_key, msg_id, slot):
        return None
    if not msg or not msg.file:
        return None
    blocks = plan_visible_blocks(channel_key, msg)
    if blocks is None:
        return None
    _visible_messages[msg.id] = msg
    _visible_blocks_by_id[msg.id] = blocks
    return take_visible_video_block(channel_key, msg.id)


def visible_slot_still_needs_plan(channel_key: str, msg_id: int, slot: int) -> bool:
    return (
        active_visible_channel() == channel_key
        and _slot_msg_ids[slot] == msg_id
        and msg_id in _visible_ids
        and msg_id not in _visible_blocks_by_id
    )


def take_queued_visible_job(channel_key: str, slot: int):
    for msg_id in _visible_ids:
        if msg_busy_in_other_slot(msg_id, slot):
            continue
        job = take_visible_video_block(channel_key, msg_id)
        if job is not None:
            return job
    return None


def take_visible_video_block(channel_key: str, msg_id: int | None):
    if msg_id is None or msg_id not in _visible_blocks_by_id:
        return None
    blocks = _visible_blocks_by_id[msg_id]
    if not blocks:
        return None
    return channel_key, _visible_messages[msg_id], blocks.pop(0)


async def find_next_visible_video(slot: int = 0):
    while True:
        if active_visible_channel() is None:
            return None
        pass_id = _visible_pass_id
        msg_id = take_next_visible_id()
        if msg_id is None:
            return None
        msg = await resolve_visible_message(msg_id)
        if pass_id != _visible_pass_id:
            continue
        if not msg or not msg.file:
            continue
        if not reserve_visible_budget(msg):
            continue
        if msg_busy_in_other_slot(msg.id, slot):
            continue
        channel_key = active_visible_channel()
        if channel_key is None:
            return None
        blocks = plan_visible_blocks(channel_key, msg)
        if blocks is None:
            continue
        if not blocks:
            continue
        return msg, blocks


def plan_visible_blocks(channel_key: str, msg):
    if not reserve_visible_budget(msg):
        return None
    # Blocks this video already has may be the oldest in the cache; refresh
    # their LRU age so the visible set's own downloads evict other videos'
    # blocks instead of these. Videos later in the visible list stay exposed
    # until their own turn to be planned.
    cache.touch_video_blocks(channel_key, msg.id)
    blocks, _ = build_uncached_blocks(channel_key, msg.id, msg.file.size)
    return blocks


def reserve_visible_budget(msg) -> bool:
    global _visible_bytes_planned
    if msg.id in _visible_budget_ids:
        return True
    # Budget by full file size so the visible set never evicts itself.
    if _visible_bytes_planned + msg.file.size > cache.MAX_BYTES:
        return False
    _visible_bytes_planned += msg.file.size
    _visible_budget_ids.add(msg.id)
    return True


def take_next_visible_id():
    global _visible_index
    if _visible_index >= len(_visible_ids):
        return None
    msg_id = _visible_ids[_visible_index]
    _visible_index += 1
    return msg_id


async def resolve_visible_message(msg_id: int):
    try:
        return await telegram.get_message(msg_id)
    except Exception:
        report_error(f"resolving visible video {msg_id}")
        return None


def active_visible_channel() -> str | None:
    """Return the visible list's channel only while it is still active."""
    channel_key = channels.active_key()
    if channel_key is None or channel_key != _visible_channel:
        return None
    return channel_key


def visible_pass_still_on(channel_key: str, msg_id: int) -> bool:
    """True while the visible walk has not moved past the given video.

    A cancelled visible-tier download must only requeue its block into
    _visible_blocks when that list still belongs to the same video: a
    concurrent set_visible() resets _visible_message/_visible_blocks without
    cancelling an in-flight download, so a later cancellation must not
    re-add a stale index onto a reset (or already-reassigned) walk.
    """
    return (
        msg_id in _visible_messages
        and active_visible_channel() == channel_key
    )


def reset_visible_pass() -> None:
    global _visible_index, _visible_pass_id, _visible_blocks, _visible_message
    global _visible_bytes_planned
    global _visible_budget_ids, _visible_blocks_by_id, _visible_messages
    _visible_index = 0
    _visible_pass_id += 1
    _visible_blocks = []
    _visible_message = None
    _visible_bytes_planned = 0
    _visible_budget_ids = set()
    _visible_blocks_by_id = {}
    _visible_messages = {}


# --- prewarm pass ---


async def select_prewarm_job(slot: int = 0):
    channel_key = channels.active_key()
    if channel_key is None:
        return None
    current_id = _slot_msg_ids[slot] if slot < len(_slot_msg_ids) else None
    current_job = take_prewarm_video_block(channel_key, current_id)
    if current_job is not None:
        return current_job
    queued_job = take_queued_prewarm_job(channel_key, slot)
    if queued_job is not None:
        return queued_job
    while True:
        video = await find_next_prewarm_video(slot)
        if video is None:
            return None
        msg, blocks = video
        _prewarm_messages[msg.id] = msg
        _prewarm_blocks_by_id[msg.id] = blocks
        job = take_prewarm_video_block(channel_key, msg.id)
        if job is not None:
            return job


def take_queued_prewarm_job(channel_key: str, slot: int):
    for msg_id in _prewarm_messages:
        if msg_busy_in_other_slot(msg_id, slot):
            continue
        job = take_prewarm_video_block(channel_key, msg_id)
        if job is not None:
            return job
    return None


def take_prewarm_video_block(channel_key: str, msg_id: int | None):
    if msg_id is None or msg_id not in _prewarm_blocks_by_id:
        return None
    blocks = _prewarm_blocks_by_id[msg_id]
    if not blocks:
        return None
    return channel_key, _prewarm_messages[msg_id], blocks.pop(0)


async def find_next_prewarm_video(slot: int = 0):
    while True:
        video = await take_next_video()
        if video is None:
            return None
        msg = await resolve_prewarm_message(video["id"])
        if not msg or not msg.file:
            continue
        if msg_busy_in_other_slot(msg.id, slot):
            continue
        channel_key = channels.active_key()
        if channel_key is None:
            return None
        blocks, remaining = build_uncached_blocks(channel_key, msg.id, msg.file.size)
        if not blocks:
            continue
        if exceeds_cache_cap(remaining):
            finish_prewarm_pass()
            return None
        return msg, blocks


async def take_next_video():
    global _prewarm_page_index
    if _prewarm_page_index >= len(_prewarm_page):
        loaded = await load_next_prewarm_page()
        if not loaded:
            return None
    video = _prewarm_page[_prewarm_page_index]
    _prewarm_page_index += 1
    return video


async def load_next_prewarm_page() -> bool:
    global _prewarm_page, _prewarm_page_index, _prewarm_cursor, _prewarm_is_last_page
    if _prewarm_is_last_page:
        finish_prewarm_pass()
        return False
    try:
        page = await telegram.list_videos(limit=50, before_id=_prewarm_cursor)
    except Exception:
        report_error("listing prewarm videos")
        finish_prewarm_pass()
        return False
    if page is None:
        print("PREFETCH listing prewarm videos failed; will retry at next rescan")
        finish_prewarm_pass()
        return False
    if not page:
        finish_prewarm_pass()
        return False
    _prewarm_page = page
    _prewarm_page_index = 0
    _prewarm_cursor = page[-1]["id"]
    _prewarm_is_last_page = len(page) < 50
    return True


async def resolve_prewarm_message(msg_id: int):
    try:
        return await telegram.get_message(msg_id)
    except Exception:
        report_error(f"resolving prewarm video {msg_id}")
        return None


async def sleep_before_rescan() -> None:
    try:
        await asyncio.wait_for(
            _work_available.wait(), timeout=config.PREWARM_RESCAN_SECONDS
        )
    except asyncio.TimeoutError:
        reset_visible_pass()
        reset_prewarm_pass()
    except asyncio.CancelledError:
        raise
    finally:
        _work_available.clear()


def finish_prewarm_pass() -> None:
    global _prewarm_page, _prewarm_page_index, _prewarm_is_last_page
    _prewarm_page = []
    _prewarm_page_index = 0
    _prewarm_is_last_page = True


def reset_prewarm_pass() -> None:
    global _prewarm_page, _prewarm_page_index, _prewarm_cursor
    global _prewarm_is_last_page, _prewarm_blocks, _prewarm_message
    global _prewarm_blocks_by_id, _prewarm_messages
    _prewarm_page = []
    _prewarm_page_index = 0
    _prewarm_cursor = None
    _prewarm_is_last_page = False
    _prewarm_blocks = []
    _prewarm_message = None
    _prewarm_blocks_by_id = {}
    _prewarm_messages = {}
    _failed_keys.clear()


# --- pure job selection ---


def select_pin_block(channel_key: str, msg_id: int, playhead: int, file_size: int) -> int | None:
    """Choose the first missing block from playhead to end, then wrap."""
    block_count = (file_size + config.BLOCK_SIZE - 1) // config.BLOCK_SIZE
    if block_count <= 0:
        return None
    cached_blocks = set()
    for idx in range(block_count):
        if cache.has_block(channel_key, msg_id, idx) or (msg_id, idx) in _failed_keys:
            cached_blocks.add(idx)
    return select_missing_pin_block(playhead, block_count, cached_blocks)


def select_missing_pin_block(
    playhead: int, block_count: int, cached_blocks: set[int]
) -> int | None:
    """Pure pin-tier job selection over a snapshot of cached indices."""
    if block_count <= 0:
        return None
    playhead = min(max(playhead, 0), block_count - 1)
    for idx in build_pin_order(playhead, block_count):
        if idx not in cached_blocks:
            return idx
    return None


def build_pin_order(playhead: int, block_count: int) -> list[int]:
    return list(range(playhead, block_count)) + list(range(0, playhead))


def build_uncached_blocks(channel_key: str, msg_id: int, file_size: int) -> tuple[list[int], int]:
    blocks = []
    remaining = 0
    block_count = (file_size + config.BLOCK_SIZE - 1) // config.BLOCK_SIZE
    for idx in range(block_count):
        if cache.has_block(channel_key, msg_id, idx):
            continue
        remaining += min(config.BLOCK_SIZE, file_size - idx * config.BLOCK_SIZE)
        if (msg_id, idx) not in _failed_keys:
            blocks.append(idx)
    return blocks, remaining


def exceeds_cache_cap(remaining: int) -> bool:
    return would_exceed_cache_cap(cache.current_total(), remaining, cache.MAX_BYTES)


def would_exceed_cache_cap(current: int, remaining: int, maximum: int) -> bool:
    """Return whether completing one video would cross the cache cap."""
    return current + remaining > maximum


def report_error(context: str) -> None:
    print(f"PREFETCH ERROR {context}:\n{traceback.format_exc()}")
