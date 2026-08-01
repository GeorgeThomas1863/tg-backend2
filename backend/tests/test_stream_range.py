"""
Async tests for telegram.stream_range — the 4096-alignment math that the
whole streaming feature hangs on. telegram.client.iter_download is replaced
with a fake serving slices of a known buffer, honoring its `offset` kwarg
(matching the real call: iter_download(msg.media, offset=..., request_size=...)).
"""

from types import SimpleNamespace

import telegram
from config import ALIGN

BUFFER = bytes(range(256)) * 79  # 20224 bytes, deterministic pattern


async def test_unaligned_start_discards_leading_remainder(monkeypatch):
    install_fake_iter_download(monkeypatch)
    out = await drain(telegram.stream_range(make_msg(), 5000, 9999))
    assert out == BUFFER[5000:10000]


async def test_aligned_start_skips_nothing(monkeypatch):
    install_fake_iter_download(monkeypatch)
    out = await drain(telegram.stream_range(make_msg(), 8192, 12000))
    assert out == BUFFER[8192:12001]


async def test_skip_spanning_multiple_chunks(monkeypatch):
    # start floors to 8192, so 3000 bytes must be skipped across three
    # 1000-byte chunks before the first emitted byte.
    install_fake_iter_download(monkeypatch, chunk_size=1000)
    start = ALIGN * 2 + 3000  # 11192
    out = await drain(telegram.stream_range(make_msg(), start, 13191))
    assert out == BUFFER[start:13192]


async def test_tail_is_trimmed_when_final_chunk_overshoots(monkeypatch):
    # end=1499 lands mid-chunk: the second 1000-byte chunk must be cut to 500.
    install_fake_iter_download(monkeypatch, chunk_size=1000)
    out = await drain(telegram.stream_range(make_msg(), 0, 1499))
    assert out == BUFFER[0:1500]


async def test_single_byte_range_yields_exactly_one_byte(monkeypatch):
    install_fake_iter_download(monkeypatch)
    out = await drain(telegram.stream_range(make_msg(), 5000, 5000))
    assert out == BUFFER[5000:5001]


async def test_download_offset_is_aligned_to_4096(monkeypatch):
    # Telegram rejects unaligned offsets; the floored offset is the contract.
    seen = install_fake_iter_download(monkeypatch)
    await drain(telegram.stream_range(make_msg(), 5000, 5999))
    assert seen["offset"] == (5000 // ALIGN) * ALIGN


async def test_midstream_exception_truncates_without_raising(monkeypatch):
    async def exploding_iter_download(media, offset, request_size):
        yield BUFFER[offset:offset + 1000]
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(telegram.client, "iter_download", exploding_iter_download)

    # Draining must not raise — mid-stream truncation is the contract.
    out = await drain(telegram.stream_range(make_msg(), 0, 9999))

    assert out == BUFFER[0:1000]
    assert BUFFER[0:10000].startswith(out)


# --- helpers ---


async def drain(agen) -> bytes:
    parts = []
    async for chunk in agen:
        parts.append(chunk)
    return b"".join(parts)


def make_msg():
    return SimpleNamespace(id=1, media=object())


def install_fake_iter_download(monkeypatch, chunk_size=None):
    """
    Serve BUFFER[offset:] in chunks (of `chunk_size`, or `request_size` when
    unset). Returns a dict recording the kwargs of the last call.
    """
    seen = {}

    async def fake_iter_download(media, offset, request_size):
        seen["offset"] = offset
        seen["request_size"] = request_size
        size = chunk_size or request_size
        data = BUFFER[offset:]
        for i in range(0, len(data), size):
            yield data[i:i + size]

    monkeypatch.setattr(telegram.client, "iter_download", fake_iter_download)
    return seen
