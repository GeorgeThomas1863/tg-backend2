"""
Cache-aware HTTP range streaming.

stream_range walks the requested range block by block via prefetch.get_block and
notes the playhead. Any failed block falls back to telegram.stream_range so the
cache layer never makes playback worse.
"""

from typing import AsyncGenerator, NamedTuple

import prefetch
import telegram
from config import BLOCK_SIZE


async def stream_range(channel_key: str, msg, start: int, end: int) -> AsyncGenerator[bytes, None]:
    """Yield bytes [start, end] inclusive: cache-first, else download+cache."""
    position = start
    for plan in plan_blocks(start, end, msg.file.size):
        data = await prefetch.get_block(channel_key, msg, plan.idx, urgent=True)
        if data is None:
            async for chunk in telegram.stream_range(msg, position, end):
                yield chunk
            return
        prefetch.note_playhead(channel_key, msg.id, plan.idx)
        piece = data[plan.start:plan.end]
        position += len(piece)
        yield piece

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
