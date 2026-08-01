"""
Parallel block download with everything network-shaped faked: ensure_pool
returns dummy senders, resolve_location returns a sentinel, and
telegram.client._call serves slices of a deterministic buffer. Verifies
striping, reassembly order, round-robin sender use, the file-reference
retry, and pool-failure fallback (None).
"""

from types import SimpleNamespace

from telethon import errors

import downloader
import telegram
from config import BLOCK_SIZE, REQUEST_SIZE

FILE_SIZE = BLOCK_SIZE + 100_000          # two blocks; second one short
BUFFER = bytes(range(256)) * (FILE_SIZE // 256 + 1)
LOCATION = object()


async def test_downloads_a_full_block_byte_exact(monkeypatch):
    install_fakes(monkeypatch)
    data = await downloader.download_block(make_msg(), 0)
    assert data == BUFFER[:BLOCK_SIZE]


async def test_downloads_short_final_block(monkeypatch):
    install_fakes(monkeypatch)
    data = await downloader.download_block(make_msg(), 1)
    assert data == BUFFER[BLOCK_SIZE:FILE_SIZE]


async def test_requests_are_striped_at_request_size_and_aligned(monkeypatch):
    seen = install_fakes(monkeypatch)
    await downloader.download_block(make_msg(), 0)
    offsets = sorted(call["offset"] for call in seen)
    assert offsets == list(range(0, BLOCK_SIZE, REQUEST_SIZE))
    for call in seen:
        assert call["offset"] % 4096 == 0
        assert call["limit"] == REQUEST_SIZE


async def test_stripes_rotate_across_pool_senders(monkeypatch):
    seen = install_fakes(monkeypatch, pool_size=2)
    await downloader.download_block(make_msg(), 0)
    senders = {call["sender"] for call in seen}
    assert len(senders) == 2


async def test_empty_pool_returns_none(monkeypatch):
    install_fakes(monkeypatch, pool_size=0)
    assert await downloader.download_block(make_msg(), 0) is None


async def test_out_of_range_block_returns_none(monkeypatch):
    install_fakes(monkeypatch)
    assert await downloader.download_block(make_msg(), 99) is None


async def test_file_reference_expiry_refreshes_and_retries(monkeypatch):
    state = {"failed_once": False, "refreshed": False}

    async def flaky_call(sender, request):
        if not state["failed_once"]:
            state["failed_once"] = True
            raise errors.FileReferenceExpiredError(request=request)
        return SimpleNamespace(bytes=BUFFER[request.offset:request.offset + request.limit])

    async def fake_get_message(msg_id):
        state["refreshed"] = True
        return make_msg()

    install_fakes(monkeypatch)
    monkeypatch.setattr(telegram.client, "_call", flaky_call)
    monkeypatch.setattr(telegram, "get_message", fake_get_message)

    data = await downloader.download_block(make_msg(), 0)

    assert state["refreshed"] is True
    assert data == BUFFER[:BLOCK_SIZE]


async def test_persistent_failure_returns_none(monkeypatch):
    async def always_failing_call(sender, request):
        raise RuntimeError("connection reset")

    install_fakes(monkeypatch)
    monkeypatch.setattr(telegram.client, "_call", always_failing_call)
    assert await downloader.download_block(make_msg(), 0) is None


# --- helpers ---


def make_msg():
    return SimpleNamespace(id=1, file=SimpleNamespace(size=FILE_SIZE), media=object())


def install_fakes(monkeypatch, pool_size=2):
    """Fake pool, location resolution, and _call. Returns the call log."""
    seen = []

    async def fake_call(sender, request):
        seen.append({"sender": sender, "offset": request.offset, "limit": request.limit})
        return SimpleNamespace(bytes=BUFFER[request.offset:request.offset + request.limit])

    async def fake_ensure_pool(dc_id):
        return [object() for _ in range(pool_size)]

    monkeypatch.setattr(downloader, "resolve_location", lambda media: (2, LOCATION))
    monkeypatch.setattr(downloader, "ensure_pool", fake_ensure_pool)
    monkeypatch.setattr(telegram.client, "_call", fake_call)
    return seen
