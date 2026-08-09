"""
Telegram layer.

Owns the shared Telethon client and everything that talks to Telegram, so the
route handlers in main.py stay purely about HTTP. The byte-range streaming
logic (including Telegram's 4096-offset-alignment requirement) lives here.
"""

import time
import traceback
from typing import AsyncGenerator, Optional

from telethon import TelegramClient
from telethon.tl.types import InputMessagesFilterVideo

import channels
import config
from config import API_ID, API_HASH, ALIGN, MSG_CACHE_TTL, REQUEST_SIZE

# Single shared client. One session, one event loop — fine for 1-2 users.
client = TelegramClient("session", API_ID, API_HASH)

_msg_cache: dict[tuple[str, int], tuple[object, float]] = {}


async def connect() -> None:
    """Start the client (interactive login on first run, reuses session after)."""
    await client.start()


async def disconnect() -> None:
    await client.disconnect()


def media_to_dict(msg) -> dict:
    """Normalise a Telethon message with media into a plain dict."""
    f = msg.file
    return {
        "id": msg.id,
        "date": msg.date.isoformat(),
        "name": f.name or f"video_{msg.id}",
        "size": f.size,
        "mime": f.mime_type or "video/mp4",
        "width": getattr(f, "width", None),
        "height": getattr(f, "height", None),
        "duration": getattr(f, "duration", None),
    }


async def list_videos(
    limit: int = 50,
    before_id: int | None = None,
    offset: int = 0,
) -> Optional[list[dict]]:
    result = await _fetch_videos(limit, before_id, offset)
    if result is None:
        return None
    videos, _ = result
    return videos


async def list_videos_with_total(
    limit: int = 50,
    before_id: int | None = None,
    offset: int = 0,
) -> Optional[tuple[list[dict], int | None]]:
    return await _fetch_videos(limit, before_id, offset)


async def _fetch_videos(
    limit: int,
    before_id: int | None,
    offset: int,
) -> Optional[tuple[list[dict], int | None]]:
    channel = channels.get_active()
    if channel is None:
        return [], None
    try:
        msgs = await client.get_messages(
            channel, limit=limit, offset_id=before_id or 0,
            add_offset=offset,
            filter=InputMessagesFilterVideo,
        )
    except Exception:
        report_error(f"listing videos from {channel!r}")
        return None
    videos = [media_to_dict(m) for m in msgs if m.file]
    total = getattr(msgs, "total", None)
    if not isinstance(total, int):
        total = None
    return videos, total


async def get_message(msg_id: int, channel_key: str | None = None):
    """Resolve a message, serving repeats from a short TTL cache."""
    channel_key = channel_key or channels.active_key()
    if channel_key is None:
        return None
    channel = config.parse_channel(channel_key)
    cached = read_cached_message(channel_key, msg_id)
    if cached is not None:
        return cached

    try:
        msg = await client.get_messages(channel, ids=msg_id)
    except Exception:
        report_error(f"fetching message {msg_id} from {channel!r}")
        return None

    if msg:
        store_cached_message(channel_key, msg_id, msg)
    return msg


def read_cached_message(channel_key: str, msg_id: int):
    cache_key = (channel_key, msg_id)
    entry = _msg_cache.get(cache_key)
    if not entry:
        return None
    msg, fetched_at = entry
    if time.monotonic() - fetched_at > MSG_CACHE_TTL:
        del _msg_cache[cache_key]
        return None
    return msg


def store_cached_message(channel_key: str, msg_id: int, msg) -> None:
    # Blunt size bound: reset rather than track LRU — refill is one RTT.
    if len(_msg_cache) > 1024:
        _msg_cache.clear()
    _msg_cache[(channel_key, msg_id)] = (msg, time.monotonic())


def invalidate_message(msg_id: int) -> None:
    """Drop a cached message (stale file_reference)."""
    for cache_key in list(_msg_cache):
        if cache_key[1] == msg_id:
            _msg_cache.pop(cache_key, None)


def clear_messages() -> None:
    """Drop every channel-specific cached Telegram message."""
    _msg_cache.clear()


async def get_thumbnail(msg) -> Optional[bytes]:
    try:
        return await client.download_media(msg, bytes, thumb=-1)
    except Exception:
        report_error(f"downloading thumbnail for message {msg.id}")
        return None


async def stream_range(msg, start: int, end: int) -> AsyncGenerator[bytes, None]:
    """
    Yield bytes [start, end] inclusive of the message's media.

    Telegram requires the download offset to be a multiple of 4096, so we floor
    start to a 4096 boundary, then discard the leading remainder before
    emitting. We also trim the tail so we never emit past `end`.
    """
    content_length = end - start + 1
    aligned_start = (start // ALIGN) * ALIGN
    to_skip = start - aligned_start
    sent = 0

    try:
        async for chunk in client.iter_download(
            msg.media,
            offset=aligned_start,
            request_size=REQUEST_SIZE,
        ):
            # Drop bytes introduced by aligning the offset downward.
            if to_skip:
                if len(chunk) <= to_skip:
                    to_skip -= len(chunk)
                    continue
                chunk = chunk[to_skip:]
                to_skip = 0

            remaining = content_length - sent
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunk = chunk[:remaining]

            sent += len(chunk)   # count what we yield, not what we received
            yield chunk

            if sent >= content_length:
                break
    except Exception:
        # Headers are already sent mid-stream; all we can do is log and stop.
        report_error(f"streaming message {msg.id} bytes {start}-{end}")


def report_error(context: str) -> None:
    """Print the current exception with context so the source is identifiable."""
    print(f"TELEGRAM ERROR {context}:\n{traceback.format_exc()}")
