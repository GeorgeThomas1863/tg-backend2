"""
stream_range orchestration over fake cache + downloader: byte-exact output
across hits/misses, single download per block under concurrency, readahead
scheduling, and fallback to telegram.stream_range when a download fails.
"""

import asyncio
from types import SimpleNamespace

import cache
import downloader
import streaming
import telegram
from config import BLOCK_SIZE, READAHEAD_BLOCKS

FILE_SIZE = 3 * BLOCK_SIZE + 1000
BUFFER = bytes(range(256)) * (FILE_SIZE // 256 + 1)


async def test_streams_exact_bytes_across_blocks(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    out = await drain(streaming.stream_range(make_msg(), 100, BLOCK_SIZE + 50))
    assert out == BUFFER[100:BLOCK_SIZE + 51]


async def test_second_read_serves_from_cache(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch)
    await drain(streaming.stream_range(make_msg(), 0, 100))
    first_count = len(downloads)
    await drain(streaming.stream_range(make_msg(), 0, 100))
    assert len(downloads) == first_count  # no new downloads


async def test_concurrent_same_block_downloads_once(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch, readahead=0)
    await asyncio.gather(
        drain(streaming.stream_range(make_msg(), 0, 100)),
        drain(streaming.stream_range(make_msg(), 0, 100)),
    )
    assert downloads.count(0) == 1


async def test_readahead_caches_upcoming_blocks(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    await drain(streaming.stream_range(make_msg(), 0, 100))  # block 0 + readahead
    await settle_readahead()
    for idx in range(1, min(READAHEAD_BLOCKS, 3) + 1):
        assert cache.has_block(1, idx) is True


async def test_download_failure_falls_back_to_direct_stream(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, failing=True)
    fallback_calls = install_fake_direct_stream(monkeypatch)

    out = await drain(streaming.stream_range(make_msg(), 0, 100))

    assert out == BUFFER[0:101]
    assert fallback_calls == [(0, 100)]


async def test_fallback_resumes_at_current_position(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch, fail_from_block=1, readahead=0)
    fallback_calls = install_fake_direct_stream(monkeypatch)

    out = await drain(streaming.stream_range(make_msg(), 100, BLOCK_SIZE + 50))

    assert out == BUFFER[100:BLOCK_SIZE + 51]
    assert fallback_calls == [(BLOCK_SIZE, BLOCK_SIZE + 50)]


# --- helpers ---


async def drain(agen) -> bytes:
    parts = []
    async for chunk in agen:
        parts.append(chunk)
    return b"".join(parts)


async def settle_readahead():
    while streaming._readahead_tasks:
        await asyncio.sleep(0)


def make_msg():
    return SimpleNamespace(id=1, file=SimpleNamespace(size=FILE_SIZE), media=object())


def install_world(tmp_path, monkeypatch, failing=False, fail_from_block=None, readahead=None):
    """Point cache at tmp_path, fake downloader.download_block, reset state."""
    downloads = []

    async def fake_download_block(msg, idx):
        downloads.append(idx)
        if failing:
            return None
        if fail_from_block is not None and idx >= fail_from_block:
            return None
        offset = idx * BLOCK_SIZE
        return BUFFER[offset:min(offset + BLOCK_SIZE, FILE_SIZE)]

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache, "MAX_BYTES", 10**12)
    monkeypatch.setattr(cache, "_total_bytes", None)
    monkeypatch.setattr(downloader, "download_block", fake_download_block)
    if readahead is not None:
        monkeypatch.setattr(streaming, "READAHEAD_BLOCKS", readahead)
    streaming._block_locks.clear()
    streaming._inflight.clear()
    return downloads


def install_fake_direct_stream(monkeypatch):
    calls = []

    async def fake_direct(msg, start, end):
        calls.append((start, end))
        yield BUFFER[start:end + 1]

    monkeypatch.setattr(telegram, "stream_range", fake_direct)
    return calls
