"""
stream_range orchestration over fake cache + downloader: byte-exact output,
single download per block under concurrency, playhead notes, and fallback to
telegram.stream_range when a download fails.
"""

import asyncio
from types import SimpleNamespace

import cache
import downloader
import prefetch
import streaming
import telegram
from config import BLOCK_SIZE

FILE_SIZE = 3 * BLOCK_SIZE + 1000
BUFFER = bytes(range(256)) * (FILE_SIZE // 256 + 1)


async def test_streams_exact_bytes_across_blocks(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    out = await drain(streaming.stream_range("test", make_msg(), 100, BLOCK_SIZE + 50))
    assert out == BUFFER[100:BLOCK_SIZE + 51]


async def test_second_read_serves_from_cache(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch)
    await drain(streaming.stream_range("test", make_msg(), 0, 100))
    first_count = len(downloads)
    await drain(streaming.stream_range("test", make_msg(), 0, 100))
    assert len(downloads) == first_count  # no new downloads


async def test_concurrent_same_block_downloads_once(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch)
    await asyncio.gather(
        drain(streaming.stream_range("test", make_msg(), 0, 100)),
        drain(streaming.stream_range("test", make_msg(), 0, 100)),
    )
    assert downloads.count(0) == 1


async def test_stream_notes_each_served_block(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    notes = []
    monkeypatch.setattr(
        prefetch,
        "note_playhead",
        lambda channel_key, msg_id, idx: notes.append((msg_id, idx)),
    )

    await drain(streaming.stream_range("test", make_msg(), 100, BLOCK_SIZE + 50))

    assert notes == [(1, 0), (1, 1)]


async def test_preview_stream_does_not_note_playhead(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    notes = []
    monkeypatch.setattr(
        prefetch,
        "note_playhead",
        lambda channel_key, msg_id, idx: notes.append((msg_id, idx)),
    )

    out = await drain(streaming.stream_range("test", make_msg(), 100, BLOCK_SIZE + 50, preview=True))

    assert out == BUFFER[100:BLOCK_SIZE + 51]  # bytes identical to a normal stream
    assert notes == []                          # but the pin never moves


async def test_download_failure_falls_back_to_direct_stream(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, failing=True)
    fallback_calls = install_fake_direct_stream(monkeypatch)

    out = await drain(streaming.stream_range("test", make_msg(), 0, 100))

    assert out == BUFFER[0:101]
    assert fallback_calls == [(0, 100)]


async def test_background_failure_is_logged_and_skipped(tmp_path, monkeypatch, capsys):
    install_world(tmp_path, monkeypatch, failing=True)
    msg = SimpleNamespace(id=7, file=SimpleNamespace(size=BLOCK_SIZE), media=object())

    await prefetch.run_worker_download("test", msg, 0)

    assert (7, 0) in prefetch._failed_keys
    assert prefetch.select_pin_block("test", 7, 0, BLOCK_SIZE) is None
    assert "PREFETCH ERROR worker block 7/0" in capsys.readouterr().out


async def test_fallback_resumes_at_current_position(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch, fail_from_block=1)
    fallback_calls = install_fake_direct_stream(monkeypatch)

    out = await drain(streaming.stream_range("test", make_msg(), 100, BLOCK_SIZE + 50))

    assert out == BUFFER[100:BLOCK_SIZE + 51]
    assert fallback_calls == [(BLOCK_SIZE, BLOCK_SIZE + 50)]


# --- helpers ---


async def drain(agen) -> bytes:
    parts = []
    async for chunk in agen:
        parts.append(chunk)
    return b"".join(parts)


def make_msg():
    return SimpleNamespace(id=1, file=SimpleNamespace(size=FILE_SIZE), media=object())


def install_world(tmp_path, monkeypatch, failing=False, fail_from_block=None):
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
    prefetch._block_locks.clear()
    prefetch._failed_keys.clear()
    return downloads


def install_fake_direct_stream(monkeypatch):
    calls = []

    async def fake_direct(msg, start, end):
        calls.append((start, end))
        yield BUFFER[start:end + 1]

    monkeypatch.setattr(telegram, "stream_range", fake_direct)
    return calls
