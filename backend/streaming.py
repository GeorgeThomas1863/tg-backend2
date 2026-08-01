"""
Cache-aware streaming orchestration.

stream_range yields an HTTP byte range by walking whole cache blocks:
hit → disk, miss → parallel download → cache → yield, while readahead
keeps the next blocks downloading behind the playhead. Any download
failure falls back to the original single-connection telegram.stream_range
for the remainder — the cache layer must never make playback worse.
"""

import asyncio
from typing import AsyncGenerator, NamedTuple

import cache
import downloader
import telegram
from config import BLOCK_SIZE, READAHEAD_BLOCKS

_block_locks: dict = {}          # (msg_id, idx) -> asyncio.Lock
_inflight: set = set()           # (msg_id, idx) readahead keys
_readahead_tasks: set = set()    # strong refs so tasks aren't GC'd
_readahead_limit = asyncio.Semaphore(2)  # never starve the live stream


async def stream_range(msg, start: int, end: int) -> AsyncGenerator[bytes, None]:
    """Yield bytes [start, end] inclusive: cache-first, else download+cache."""
    position = start
    for plan in plan_blocks(start, end, msg.file.size):
        data = await get_block(msg, plan.idx)
        if data is None:
            async for chunk in telegram.stream_range(msg, position, end):
                yield chunk
            return
        schedule_readahead(msg, plan.idx)
        piece = data[plan.start:plan.end]
        position += len(piece)
        yield piece


async def get_block(msg, idx: int) -> bytes | None:
    """One block from cache or network; concurrent callers share one fetch."""
    key = (msg.id, idx)
    lock = _block_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = cache.read_block(msg.id, idx)
        if cached is not None:
            return cached
        data = await downloader.download_block(msg, idx)
        if data is None:
            return None
        cache.write_block(msg.id, idx, data)
        return data


def schedule_readahead(msg, current_idx: int) -> None:
    """Kick off background fetches for the next READAHEAD_BLOCKS blocks."""
    last_idx = (msg.file.size - 1) // BLOCK_SIZE
    stop = min(current_idx + READAHEAD_BLOCKS, last_idx)
    for idx in range(current_idx + 1, stop + 1):
        key = (msg.id, idx)
        if key in _inflight or cache.has_block(msg.id, idx):
            continue
        _inflight.add(key)
        task = asyncio.create_task(fetch_ahead(msg, idx))
        _readahead_tasks.add(task)
        task.add_done_callback(_readahead_tasks.discard)
    prune_locks()


async def fetch_ahead(msg, idx: int) -> None:
    try:
        async with _readahead_limit:
            await get_block(msg, idx)
    finally:
        _inflight.discard((msg.id, idx))


def prune_locks() -> None:
    # Unlocked entries can be dropped; a rare double-download after a drop
    # is harmless (block writes are atomic) — correctness never depends on it.
    if len(_block_locks) < 8192:
        return
    for key in list(_block_locks):
        if not _block_locks[key].locked():
            del _block_locks[key]


# --- pure builders ---


class BlockSlice(NamedTuple):
    idx: int
    start: int  # within-block slice start
    end: int    # within-block slice end, exclusive


def plan_blocks(start: int, end: int, file_size: int) -> list[BlockSlice]:
    """Map an inclusive byte range onto block indices with in-block slices."""
    if start < 0 or end < start or start >= file_size:
        return []
    end = min(end, file_size - 1)

    plans = []
    for idx in range(start // BLOCK_SIZE, end // BLOCK_SIZE + 1):
        block_offset = idx * BLOCK_SIZE
        slice_start = max(start - block_offset, 0)
        slice_end = min(end - block_offset + 1, BLOCK_SIZE)
        plans.append(BlockSlice(idx, slice_start, slice_end))
    return plans
