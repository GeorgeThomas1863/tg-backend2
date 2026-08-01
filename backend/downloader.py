"""
Parallel MTProto download engine.

Telegram throttles per connection (~0.7 MB/s measured), so a block is
fetched as REQUEST_SIZE stripes spread over a small pool of extra senders
created from the existing session. Same-DC senders reuse the session auth
key on a fresh connection; other DCs get an exported authorization —
mirroring Telethon's own _create_exported_sender (verified in 1.44).
"""

import asyncio
import traceback

from telethon import errors, utils
from telethon.network import MTProtoSender
from telethon.tl import functions
from telethon.tl.alltlobjects import LAYER

import telegram
from config import BLOCK_SIZE, REQUEST_SIZE, TG_CONNECTIONS

_pools: dict[int, list] = {}   # dc_id -> connected senders
_pool_lock = asyncio.Lock()


async def download_block(msg, block_idx: int) -> bytes | None:
    """Download one whole block of the message's media, or None on failure."""
    if not msg or not msg.file:
        return None
    offset = block_idx * BLOCK_SIZE
    length = min(BLOCK_SIZE, msg.file.size - offset)
    if length <= 0:
        return None

    dc_id, location = resolve_location(msg.media)
    pool = await ensure_pool(dc_id)
    if not pool:
        return None

    parts = await fetch_stripes(pool, location, offset, length, msg)
    if parts is None:
        return None

    # The final stripe over-requests (limit stays 4096-aligned), so trim any
    # tail past the block; short data is a genuine failure.
    data = b"".join(parts)
    if len(data) < length:
        print(f"DOWNLOADER ERROR block {msg.id}/{block_idx}: "
              f"got {len(data)} bytes, wanted {length}")
        return None
    return data[:length]


async def disconnect_all() -> None:
    """Disconnect every pooled sender (app shutdown)."""
    async with _pool_lock:
        for pool in _pools.values():
            for sender in pool:
                try:
                    await sender.disconnect()
                except Exception:
                    report_error("disconnecting pooled sender")
        _pools.clear()


# --- striping ---


async def fetch_stripes(pool, location, offset, length, msg) -> list | None:
    tasks = []
    stripe_offsets = range(offset, offset + length, REQUEST_SIZE)
    for i, stripe_offset in enumerate(stripe_offsets):
        sender = pool[i % len(pool)]
        tasks.append(fetch_stripe(sender, location, stripe_offset, msg))
    try:
        return await asyncio.gather(*tasks)
    except Exception:
        report_error(f"striped download at offset {offset} for message {msg.id}")
        return None


async def fetch_stripe(sender, location, stripe_offset, msg) -> bytes:
    request = functions.upload.GetFileRequest(location, offset=stripe_offset, limit=REQUEST_SIZE)
    try:
        result = await telegram.client._call(sender, request)
    except (errors.FileReferenceExpiredError, errors.FilerefUpgradeNeededError):
        fresh_location = await refresh_location(msg)
        if fresh_location is None:
            raise
        request = functions.upload.GetFileRequest(fresh_location, offset=stripe_offset, limit=REQUEST_SIZE)
        result = await telegram.client._call(sender, request)
    return result.bytes


async def refresh_location(msg):
    """Re-resolve the message for a fresh file_reference; None if gone."""
    telegram.invalidate_message(msg.id)
    fresh = await telegram.get_message(msg.id)
    if not fresh or not fresh.media:
        return None
    return resolve_location(fresh.media)[1]


# --- sender pool ---


async def ensure_pool(dc_id: int) -> list:
    """Return the connected sender pool for a DC, building it on first use."""
    async with _pool_lock:
        if dc_id in _pools:
            return _pools[dc_id]
        pool = []
        for _ in range(TG_CONNECTIONS):
            sender = await create_sender(dc_id)
            if sender:
                pool.append(sender)
        _pools[dc_id] = pool
        return pool


async def create_sender(dc_id: int):
    """Connect one extra MTProto sender for a DC, or None on failure.

    Same-DC senders must reuse the session auth key — Telegram rejects
    auth.exportAuthorization for the DC you are connected to (DC_ID_INVALID).
    Every new connection still needs initConnection as its first request,
    so both paths send InvokeWithLayer(init) before any file request.
    """
    client = telegram.client
    try:
        dc = await client._get_dc(dc_id)
        is_same_dc = dc_id == client.session.dc_id
        auth_key = client.session.auth_key if is_same_dc else None
        sender = MTProtoSender(auth_key, loggers=client._log)
        await sender.connect(client._connection(
            dc.ip_address, dc.port, dc.id,
            loggers=client._log, proxy=client._proxy, local_addr=client._local_addr,
        ))
        if is_same_dc:
            client._init_request.query = functions.help.GetConfigRequest()
        else:
            auth = await client(functions.auth.ExportAuthorizationRequest(dc_id))
            client._init_request.query = functions.auth.ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes)
        await sender.send(functions.InvokeWithLayerRequest(LAYER, client._init_request))
        return sender
    except Exception:
        report_error(f"creating sender for DC {dc_id}")
        return None


# --- pure builders ---


def resolve_location(media):
    """(dc_id, input_location) for a message's media. Seam for tests."""
    return utils.get_input_location(media)


def report_error(context: str) -> None:
    print(f"DOWNLOADER ERROR {context}:\n{traceback.format_exc()}")
