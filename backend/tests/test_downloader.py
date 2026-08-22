"""
Parallel block download with everything network-shaped faked: ensure_pool
returns dummy senders, resolve_location returns a sentinel, and
telegram.client._call serves slices of a deterministic buffer. Verifies
striping, reassembly order, round-robin sender use, the file-reference
retry, and pool-failure fallback (None).
"""

import asyncio
import struct
from types import SimpleNamespace

import pytest
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


async def test_empty_pool_raises_pool_unavailable(monkeypatch):
    install_fakes(monkeypatch, pool_size=0)
    with pytest.raises(downloader.PoolUnavailable) as info:
        await downloader.download_block(make_msg(), 0)
    assert info.value.retry_after >= 1


async def test_disabled_pool_raises_pool_unavailable_for_a_retry_window(monkeypatch):
    """A disabled pool is a hold for the worker, never a failed block."""
    install_fakes(monkeypatch, pool_size=0)
    monkeypatch.setattr(downloader, "_target_connections", 0)
    with pytest.raises(downloader.PoolUnavailable) as info:
        await downloader.download_block(make_msg(), 0)
    assert info.value.retry_after == downloader.POOL_RETRY_SECONDS


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


# --- sender pool lifecycle ---


async def test_configure_shrink_disconnects_extra_senders(monkeypatch):
    install_pool_fakes(monkeypatch)
    downloader.configure(3)
    pool = await downloader.ensure_pool(2)
    original_senders = list(pool)

    downloader.configure(1)
    trimmed = await downloader.ensure_pool(2)

    assert len(trimmed) == 1
    assert [sender.disconnect_calls for sender in original_senders] == [0, 1, 1]


async def test_configure_grow_tops_pool_up(monkeypatch):
    state = install_pool_fakes(monkeypatch)
    downloader.configure(1)
    assert len(await downloader.ensure_pool(2)) == 1

    downloader.configure(3)
    assert len(await downloader.ensure_pool(2)) == 3
    assert state["created"] == 3


async def test_dead_sender_is_pruned_and_replaced_after_cooldown(monkeypatch):
    state = install_pool_fakes(monkeypatch)
    pool = await downloader.ensure_pool(2)
    assert len(pool) == 2
    pool[0].connected = False

    assert len(await downloader.ensure_pool(2)) == 1
    assert state["created"] == 2

    state["now"] += downloader.POOL_RETRY_SECONDS
    refilled = await downloader.ensure_pool(2)
    assert len(refilled) == 2
    assert all(sender.is_connected() for sender in refilled)
    assert state["created"] == 3


async def test_all_senders_dead_raises_pool_unavailable_until_cooldown_ends(monkeypatch):
    state = install_pool_fakes(monkeypatch)
    install_call_fakes(monkeypatch)
    for sender in await downloader.ensure_pool(2):
        sender.connected = False

    with pytest.raises(downloader.PoolUnavailable) as info:
        await downloader.download_block(make_msg(), 0)
    assert 0 < info.value.retry_after <= downloader.POOL_RETRY_SECONDS

    state["now"] += downloader.POOL_RETRY_SECONDS
    assert await downloader.download_block(make_msg(), 0) == BUFFER[:BLOCK_SIZE]


async def test_sender_creation_failure_starts_cooldown(monkeypatch):
    state = install_pool_fakes(monkeypatch, create_ok=False)
    assert await downloader.ensure_pool(2) == []
    assert await downloader.ensure_pool(2) == []
    assert state["attempts"] == 1

    state["create_ok"] = True
    state["now"] += downloader.POOL_RETRY_SECONDS
    assert len(await downloader.ensure_pool(2)) == 2


async def test_sender_dying_mid_block_raises_pool_unavailable(monkeypatch):
    state = install_pool_fakes(monkeypatch)
    monkeypatch.setattr(downloader, "resolve_location", lambda media: (2, LOCATION))

    async def dying_call(sender, request):
        sender.connected = False
        raise ConnectionError("Cannot send requests while disconnected")

    monkeypatch.setattr(telegram.client, "_call", dying_call)
    await downloader.ensure_pool(2)

    with pytest.raises(downloader.PoolUnavailable) as info:
        await downloader.download_block(make_msg(), 0)
    assert info.value.retry_after >= 1
    assert await downloader.ensure_pool(2) == []
    assert state["created"] == 2


async def test_sender_trimmed_mid_block_raises_pool_unavailable_without_cooldown(monkeypatch):
    """Lowering the runtime target disconnects a busy sender; the block is retried, not marked bad."""
    state = install_pool_fakes(monkeypatch)
    monkeypatch.setattr(downloader, "resolve_location", lambda media: (2, LOCATION))
    trimmed = {"done": False}

    async def trimming_call(sender, request):
        if not trimmed["done"]:
            trimmed["done"] = True
            downloader.configure(1)
            await downloader.ensure_pool(2)  # a concurrent block trims the shared pool
        if not sender.is_connected():
            raise ConnectionError("Cannot send requests while disconnected")
        return SimpleNamespace(bytes=BUFFER[request.offset:request.offset + request.limit])

    monkeypatch.setattr(telegram.client, "_call", trimming_call)
    await downloader.ensure_pool(2)

    with pytest.raises(downloader.PoolUnavailable):
        await downloader.download_block(make_msg(), 0)
    assert downloader.pool_retry_after(2) == 0
    assert len(await downloader.ensure_pool(2)) == 1
    assert state["created"] == 2


async def test_block_failure_during_cooldown_raises_pool_unavailable(monkeypatch):
    """A second block failing right after the first pruned the pool is the same outage."""
    state = install_pool_fakes(monkeypatch)
    monkeypatch.setattr(downloader, "resolve_location", lambda media: (2, LOCATION))

    async def failing_call(sender, request):
        raise ConnectionError("Cannot send requests while disconnected")

    monkeypatch.setattr(telegram.client, "_call", failing_call)
    await downloader.ensure_pool(2)
    downloader._pool_retry_at[2] = state["now"] + 10

    with pytest.raises(downloader.PoolUnavailable):
        await downloader.download_block(make_msg(), 0)


async def test_failed_block_waits_for_every_stripe_before_returning(monkeypatch):
    """No stripe is left dangling with an unretrieved exception after a failure."""
    install_fakes(monkeypatch)
    finished = []

    async def uneven_call(sender, request):
        if request.offset == 0:
            raise ConnectionError("first stripe died")
        for _ in range(5):
            await asyncio.sleep(0)
        finished.append(request.offset)
        return SimpleNamespace(bytes=BUFFER[request.offset:request.offset + request.limit])

    monkeypatch.setattr(telegram.client, "_call", uneven_call)
    assert await downloader.download_block(make_msg(), 0) is None
    assert len(finished) == BLOCK_SIZE // REQUEST_SIZE - 1


async def test_pool_cooldown_is_logged_once(monkeypatch, capsys):
    install_pool_fakes(monkeypatch)
    for sender in await downloader.ensure_pool(2):
        sender.connected = False
    await downloader.ensure_pool(2)
    await downloader.ensure_pool(2)
    output = capsys.readouterr().out
    assert output.count("DOWNLOADER pool") == 1


# --- flood tracking ---


def test_note_flood_deduplicates_incidents_within_retry_window(monkeypatch):
    state = install_flood_fakes(monkeypatch)

    downloader.note_flood(2)
    state["now"] += downloader.POOL_RETRY_SECONDS - 1
    downloader.note_flood(2)
    state["now"] += downloader.POOL_RETRY_SECONDS
    downloader.note_flood(2)

    assert downloader.flood_status() == {"count": 2, "last_seconds_ago": 0.0}


def test_flood_status_reports_elapsed_time(monkeypatch):
    state = install_flood_fakes(monkeypatch)
    assert downloader.flood_status() == {"count": 0, "last_seconds_ago": None}

    downloader.note_flood(2)
    state["now"] += 3.5

    assert downloader.flood_status() == {"count": 1, "last_seconds_ago": 3.5}


async def test_stripe_429_records_flood(monkeypatch):
    install_flood_fakes(monkeypatch)

    async def flooded_call(sender, request):
        raise make_flood_error()

    monkeypatch.setattr(telegram.client, "_call", flooded_call)

    assert await downloader.fetch_stripes(
        [FakeSender()], LOCATION, 0, REQUEST_SIZE, make_msg(), 2
    ) is None
    assert downloader.flood_status()["count"] == 1


async def test_pruned_flooded_sender_records_flood(monkeypatch):
    install_pool_fakes(monkeypatch)
    install_flood_fakes(monkeypatch)
    sender = FakeSender()
    sender.connected = False
    sender._disconnected = asyncio.get_running_loop().create_future()
    sender._disconnected.set_exception(make_flood_error())
    downloader._pools[2] = [sender]

    await downloader.ensure_pool(2)

    assert downloader.flood_status()["count"] == 1


async def test_pruned_sender_without_disconnected_future_is_not_flood(monkeypatch):
    install_pool_fakes(monkeypatch)
    install_flood_fakes(monkeypatch)
    sender = FakeSender()
    sender.connected = False
    downloader._pools[2] = [sender]

    await downloader.ensure_pool(2)

    assert downloader.flood_status()["count"] == 0


# --- helpers ---


def make_msg():
    return SimpleNamespace(id=1, file=SimpleNamespace(size=FILE_SIZE), media=object())


def install_fakes(monkeypatch, pool_size=2):
    """Fake pool, location resolution, and _call. Returns the call log."""
    async def fake_ensure_pool(dc_id):
        return [FakeSender() for _ in range(pool_size)]

    monkeypatch.setattr(downloader, "ensure_pool", fake_ensure_pool)
    return install_call_fakes(monkeypatch)


def install_call_fakes(monkeypatch):
    """Fake location resolution and _call only; the real pool code stays."""
    seen = []

    async def fake_call(sender, request):
        seen.append({"sender": sender, "offset": request.offset, "limit": request.limit})
        return SimpleNamespace(bytes=BUFFER[request.offset:request.offset + request.limit])

    monkeypatch.setattr(downloader, "resolve_location", lambda media: (2, LOCATION))
    monkeypatch.setattr(telegram.client, "_call", fake_call)
    return seen


class FakeSender:
    def __init__(self):
        self.connected = True
        self.disconnect_calls = 0

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False


def install_pool_fakes(monkeypatch, create_ok=True):
    """Real ensure_pool over fake senders and a controllable clock."""
    import asyncio

    state = {"now": 1000.0, "created": 0, "attempts": 0, "create_ok": create_ok}

    async def fake_create_sender(dc_id):
        state["attempts"] += 1
        if not state["create_ok"]:
            return None
        state["created"] += 1
        return FakeSender()

    monkeypatch.setattr(downloader, "_pools", {})
    monkeypatch.setattr(downloader, "_pool_retry_at", {})
    monkeypatch.setattr(downloader, "_pool_lock", asyncio.Lock())
    monkeypatch.setattr(downloader, "create_sender", fake_create_sender)
    monkeypatch.setattr(downloader, "now", lambda: state["now"])
    monkeypatch.setattr(downloader, "_target_connections", 2)
    return state


def install_flood_fakes(monkeypatch):
    state = {"now": 1000.0}
    monkeypatch.setattr(downloader, "_flood_count", 0)
    monkeypatch.setattr(downloader, "_flood_last_at", None)
    monkeypatch.setattr(downloader, "now", lambda: state["now"])
    return state


def make_flood_error():
    return errors.common.InvalidBufferError(struct.pack("<i", -429))
