"""
Parallel MTProto download engine.

Telegram throttles per connection (~0.7 MB/s measured), so a block is
fetched as REQUEST_SIZE stripes spread over a small pool of extra senders
created from the existing session. Same-DC senders reuse the session auth
key on a fresh connection; other DCs get an exported authorization —
mirroring Telethon's own _create_exported_sender (verified in 1.44).

Pooled senders can die for good: on a transport-level flood (HTTP 429)
Telethon permanently disconnects the sender instead of reconnecting, so
every later request on it fails instantly. ensure_pool prunes dead senders
on each use and refills the pool after POOL_RETRY_SECONDS — the cooldown
stops us from immediately reopening connections into the same flood limit.
While the pool is disabled (runtime target 0) or empty, download_block
raises PoolUnavailable so callers wait or fall back instead of failing.
"""

import asyncio
import time
import traceback

from telethon import errors, utils
from telethon.network import MTProtoSender
from telethon.tl import functions
from telethon.tl.alltlobjects import LAYER

import telegram
from config import BLOCK_SIZE, POOL_RETRY_SECONDS, REQUEST_SIZE, TG_CONNECTIONS

_pools: dict[int, list] = {}           # dc_id -> connected senders
_pool_lock = asyncio.Lock()
_pool_retry_at: dict[int, float] = {}  # dc_id -> now() before which no sender is created
_target_connections: int = TG_CONNECTIONS
_flood_count: int = 0
_flood_last_at: float | None = None


class PoolUnavailable(Exception):
    """No connected sender for the DC right now; retry_after says how long to wait."""

    def __init__(self, dc_id: int, retry_after: float):
        super().__init__(f"no connected download sender for DC {dc_id}; retry in {retry_after:.0f}s")
        self.dc_id = dc_id
        self.retry_after = retry_after


async def download_block(msg, block_idx: int) -> bytes | None:
    """Download one whole block of the message's media, or None on failure.

    Raises PoolUnavailable when the pool is disabled or has no connected
    sender, so background callers can wait instead of marking the block bad.
    """
    if not msg or not msg.file:
        return None
    offset = block_idx * BLOCK_SIZE
    length = min(BLOCK_SIZE, msg.file.size - offset)
    if length <= 0:
        return None

    dc_id, location = resolve_location(msg.media)
    pool = await ensure_pool(dc_id)
    if not pool:
        raise PoolUnavailable(dc_id, empty_pool_retry_after(dc_id))

    # Snapshot: a concurrent ensure_pool may trim the shared list mid-block.
    senders = list(pool)
    parts = await fetch_stripes(senders, location, offset, length, msg, dc_id)
    if parts is None:
        raise_if_senders_lost(dc_id, pool, senders)
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
        _pool_retry_at.clear()


# --- striping ---


async def fetch_stripes(pool, location, offset, length, msg, dc_id=None) -> list | None:
    tasks = []
    stripe_offsets = range(offset, offset + length, REQUEST_SIZE)
    for i, stripe_offset in enumerate(stripe_offsets):
        sender = pool[i % len(pool)]
        tasks.append(fetch_stripe(sender, location, stripe_offset, msg))
    # return_exceptions keeps every stripe awaited, so a failure never leaves
    # sibling futures dangling with "exception was never retrieved" noise.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failure = first_failure(results)
    if failure is None:
        return results
    if is_flood_error(failure):
        flood_dc_id = dc_id if dc_id is not None else resolve_location(msg.media)[0]
        note_flood(flood_dc_id)
    report_error(f"striped download at offset {offset} for message {msg.id}", failure)
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
    """Return the connected senders for a DC, pruning dead ones and refilling after the cooldown."""
    async with _pool_lock:
        pool = _pools.setdefault(dc_id, [])
        note_flood_deaths(dc_id, pool)
        dropped = prune_dead_senders(pool)
        if dropped:
            start_cooldown(dc_id, f"{dropped} sender(s) disconnected")
        await trim_pool(pool)
        if len(pool) < target_connections() and pool_retry_after(dc_id) <= 0:
            await fill_pool(dc_id, pool)
        return pool


def empty_pool_retry_after(dc_id: int) -> float:
    """Wait for an empty pool: a full retry window while disabled, else the cooldown."""
    if target_connections() == 0:
        return float(POOL_RETRY_SECONDS)
    return max(pool_retry_after(dc_id), 1.0)


def raise_if_senders_lost(dc_id: int, pool: list, senders: list) -> None:
    """A block that failed because its senders died or were trimmed is a pool change, not a bad block.

    A failure inside an active cooldown counts too: a concurrent block has
    usually already pruned the same dead senders. A sender trimmed away by a
    lowered runtime target is disconnected mid-request; the block is retried.
    """
    note_flood_deaths(dc_id, pool)
    dropped = prune_dead_senders(pool)
    if dropped:
        start_cooldown(dc_id, f"{dropped} sender(s) disconnected mid-block")
    if dropped or pool_retry_after(dc_id) > 0 or any_sender_trimmed(senders, pool):
        raise PoolUnavailable(dc_id, max(pool_retry_after(dc_id), 1.0))


def any_sender_trimmed(senders: list, pool: list) -> bool:
    """True when a sender this block used is no longer in the shared pool."""
    for sender in senders:
        if sender not in pool:
            return True
    return False


def prune_dead_senders(pool: list) -> int:
    """Drop senders Telethon has permanently disconnected; return how many."""
    alive = []
    for sender in pool:
        if sender.is_connected():
            alive.append(sender)
    dropped = len(pool) - len(alive)
    pool[:] = alive
    return dropped


async def trim_pool(pool: list) -> None:
    """Disconnect and remove senders beyond the runtime target."""
    extras = pool[target_connections():]
    del pool[target_connections():]
    for sender in extras:
        try:
            await sender.disconnect()
        except Exception:
            report_error("disconnecting excess pooled sender")


async def fill_pool(dc_id: int, pool: list) -> None:
    """Top the pool up to the runtime target; failures start the cooldown."""
    while len(pool) < target_connections():
        sender = await create_sender(dc_id)
        if sender is None:
            start_cooldown(dc_id, "creating a sender failed")
            return
        pool.append(sender)


def start_cooldown(dc_id: int, reason: str) -> None:
    _pool_retry_at[dc_id] = now() + POOL_RETRY_SECONDS
    print(f"DOWNLOADER pool for DC {dc_id}: {reason}; "
          f"no new connections for {POOL_RETRY_SECONDS}s")


def pool_retry_after(dc_id: int) -> float:
    """Seconds until new senders may be created for the DC (0 when allowed now)."""
    return max(0.0, _pool_retry_at.get(dc_id, 0.0) - now())


def now() -> float:
    return time.monotonic()


def configure(connections: int) -> None:
    """Set the runtime download-pool target."""
    global _target_connections
    _target_connections = connections


def target_connections() -> int:
    return _target_connections


def flood_status() -> dict:
    seconds_ago = None
    if _flood_last_at is not None:
        seconds_ago = now() - _flood_last_at
    return {"count": _flood_count, "last_seconds_ago": seconds_ago}


def note_flood(dc_id: int) -> None:
    """Record one deduplicated transport-level flood incident."""
    global _flood_count, _flood_last_at
    observed_at = now()
    is_new = (
        _flood_last_at is None
        or observed_at - _flood_last_at >= POOL_RETRY_SECONDS
    )
    _flood_last_at = observed_at
    if not is_new:
        return
    _flood_count += 1
    print(
        f"DOWNLOADER flood: Telegram sent transport-level 429 on DC {dc_id} "
        f"(incident #{_flood_count})"
    )


def note_flood_deaths(dc_id: int, pool: list) -> None:
    for sender in pool:
        if sender_died_of_flood(sender):
            note_flood(dc_id)


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


def first_failure(results: list):
    """The first exception in a gather(return_exceptions=True) result, or None."""
    for result in results:
        if isinstance(result, BaseException):
            return result
    return None


def sender_died_of_flood(sender) -> bool:
    disconnected = getattr(sender, "_disconnected", None)
    if disconnected is None or not disconnected.done() or disconnected.cancelled():
        return False
    try:
        error = disconnected.exception()
    except Exception:
        return False
    return is_flood_error(error)


def is_flood_error(error) -> bool:
    return isinstance(error, errors.common.InvalidBufferError) and error.code == 429


def report_error(context: str, error: BaseException | None = None) -> None:
    detail = "".join(traceback.format_exception(error)) if error else traceback.format_exc()
    print(f"DOWNLOADER ERROR {context}:\n{detail}")
