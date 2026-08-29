"""
FastAPI app. HTTP concerns only — Range parsing, status codes, headers.
All Telegram work is delegated to the telegram module.
"""

from contextlib import asynccontextmanager
from typing import Literal
import asyncio
import logging
import shutil

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StrictInt
from starlette.middleware.sessions import SessionMiddleware

import cache
import categories
import channels
import db
import downloader
import playability
import prefetch
import settings
import streaming
import telegram
import tg_auth
import video_metadata
from config import (
    AUTH_MAX_ATTEMPTS,
    AUTH_WINDOW_SECONDS,
    BACKEND_PORT,
    FRONTEND_ORIGIN,
    PW_HASH,
    SESSION_SECRET,
)
from rate_limit import AuthRateLimiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await telegram.connect()
    authorized = await telegram.is_authorized()
    await channels.startup()
    await settings.startup()
    if authorized:
        await prefetch.start()
    yield
    await prefetch.stop()
    await downloader.disconnect_all()
    await telegram.disconnect()
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
auth_limiter = AuthRateLimiter(AUTH_MAX_ATTEMPTS, AUTH_WINDOW_SECONDS)

# Signed session cookie; the login route sets "authenticated" in it.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=24 * 60 * 60,  # 24 hours
    same_site="strict",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- auth ---


class AuthBody(BaseModel):
    pw: str


class TelegramPhoneBody(BaseModel):
    phone: str


class TelegramCodeBody(BaseModel):
    code: str


class TelegramPasswordBody(BaseModel):
    password: str


class CachePausedBody(BaseModel):
    paused: bool


class VisibleVideosBody(BaseModel):
    ids: list[int]


class PriorityVideoBody(BaseModel):
    id: int


class BatchDownloadBody(BaseModel):
    category: str | None = None


class CacheSettingsBody(BaseModel):
    cache_dir: str | None = None
    cache_max_gb: float | None = None
    # Strict: Pydantic would otherwise coerce true -> 1 and "4" -> 4 before validation.
    tg_connections: StrictInt | None = None


class ChannelBody(BaseModel):
    channel: str


class ChannelIdBody(BaseModel):
    id: str


def check_password(pw: str) -> bool:
    """Compare a submitted password against the bcrypt hash from config."""
    try:
        return bcrypt.checkpw(pw.encode(), PW_HASH.encode())
    except ValueError as e:
        print(f"BCRYPT ERROR (is PW_HASH a valid bcrypt hash?): {e}")
        return False


def require_auth(request: Request) -> None:
    """Route dependency: reject requests whose session isn't authenticated."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/api/auth")
async def login(body: AuthBody, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    retry_after = auth_limiter.retry_after(client_ip)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "message": "Too many attempts. Try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )

    if not check_password(body.pw):
        auth_limiter.record_failure(client_ip)
        return {"success": False, "message": "Wrong password"}

    auth_limiter.clear(client_ip)
    request.session["authenticated"] = True
    return {"success": True, "message": "Authenticated"}


# --- Telegram account auth ---


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def telegram_auth_response(result: dict):
    retry_after = result.pop("retry_after", None)
    if retry_after is None:
        return result
    return JSONResponse(
        status_code=429,
        content=result,
        headers={"Retry-After": str(retry_after)},
    )


@app.get("/api/telegram/status", dependencies=[Depends(require_auth)])
async def telegram_status():
    try:
        return await tg_auth.status()
    except tg_auth.TelegramRequestError:
        raise HTTPException(status_code=502, detail="Telegram request failed")


@app.post("/api/telegram/login/start", dependencies=[Depends(require_auth)])
async def telegram_login_start(body: TelegramPhoneBody, request: Request):
    result = await tg_auth.start_login(body.phone, client_ip(request))
    return telegram_auth_response(result)


@app.post("/api/telegram/login/code", dependencies=[Depends(require_auth)])
async def telegram_login_code(body: TelegramCodeBody, request: Request):
    result = await tg_auth.submit_code(body.code, client_ip(request))
    return telegram_auth_response(result)


@app.post("/api/telegram/login/password", dependencies=[Depends(require_auth)])
async def telegram_login_password(body: TelegramPasswordBody, request: Request):
    result = await tg_auth.submit_password(body.password, client_ip(request))
    return telegram_auth_response(result)


@app.post("/api/telegram/logout", dependencies=[Depends(require_auth)])
async def telegram_logout():
    return await tg_auth.logout()


# --- cache ---


@app.get("/api/cache/status", dependencies=[Depends(require_auth)])
async def cache_status():
    worker = prefetch.status()
    effective_settings = settings.effective()
    try:
        total_bytes = cache.current_total()
    except Exception:
        cache.report_error("reading total for cache status")
        total_bytes = 0
    return {
        "total_bytes": total_bytes,
        "max_bytes": cache.MAX_BYTES,
        "paused": worker["paused"],
        "active": worker["active"],
        "active_slots": worker["active_slots"],
        "priority_queue": worker["priority_queue"],
        "batch": worker["batch"],
        "videos": cache.video_totals(),
        "cache_dir": effective_settings["cache_dir"],
        "max_gb": effective_settings["cache_max_gb"],
        "tg_connections": effective_settings["tg_connections"],
        "flood": downloader.flood_status(),
    }


@app.post("/api/cache/paused", dependencies=[Depends(require_auth)])
async def set_cache_paused(body: CachePausedBody):
    prefetch.set_paused(body.paused)
    return {
        "success": True,
        "message": "Caching paused" if body.paused else "Caching resumed",
    }


MAX_VISIBLE_IDS = 200


@app.post("/api/prefetch/visible", dependencies=[Depends(require_auth)])
async def set_visible_videos(body: VisibleVideosBody):
    if len(body.ids) > MAX_VISIBLE_IDS:
        return {"success": False, "message": "Too many visible videos"}
    channel_key = channels.active_key()
    if channel_key is None:
        return {"success": False, "message": "No active channel"}
    prefetch.set_visible(channel_key, body.ids)
    return {"success": True, "message": f"Tracking {len(body.ids)} visible videos"}


@app.post("/api/prefetch/priority", dependencies=[Depends(require_auth)])
async def set_priority_video(body: PriorityVideoBody):
    """Queue a video for priority download, rejecting up front whatever the
    background worker would otherwise have to silently drop later.

    Validated here rather than left to the worker: telegram.get_message is a
    short-TTL cache (the video was almost always just resolved to list it),
    so the extra round trip this adds to the request is usually free, and it
    lets the button get an immediate, honest answer instead of polling for
    one.
    """
    channel_key = channels.active_key()
    if channel_key is None:
        return {"success": False, "message": "No active channel"}

    msg = await telegram.get_message(body.id, channel_key)
    if not msg or not msg.file:
        return {"success": False, "message": "Video not found"}

    if msg.file.size > cache.MAX_BYTES:
        return {
            "success": False,
            "message": (
                f"This video is {format_gib(msg.file.size)}, larger than "
                f"the {format_gib(cache.MAX_BYTES)} cache cap"
            ),
        }

    if cache.video_totals().get(msg.id, 0) >= msg.file.size:
        return {"success": True, "message": "Already fully cached"}

    prefetch.set_priority(channel_key, body.id)
    return {"success": True, "message": f"Prioritizing video {body.id}"}


def format_gib(num_bytes: float) -> str:
    """Readable GB size for a user-facing message, e.g. '12.4 GB'."""
    return f"{num_bytes / 1024**3:.1f} GB"


@app.post("/api/prefetch/batch", dependencies=[Depends(require_auth)])
async def start_batch_download(body: BatchDownloadBody):
    channel_key = channels.active_key()
    if channel_key is None:
        return {"success": False, "message": "No active channel", "queued": 0}

    cat_start, cat_end = await _resolve_category(body.category) or (None, None)
    total, counts_exact = await _resolve_batch_total(body.category, cat_start, cat_end)
    if total is None:
        raise HTTPException(status_code=502, detail="Telegram request failed")
    queued = min(total, prefetch.BATCH_ENQUEUE_CAP)
    prefetch.set_batch(channel_key, cat_start, cat_end, queued)

    message = _build_batch_message(queued, total, counts_exact)
    return {"success": True, "message": message, "queued": queued}


async def _resolve_batch_total(
    category: str | None, cat_start: int | None, cat_end: int | None
) -> tuple[int | None, bool]:
    """Return (total, counts_exact) -- the honest denominator for a batch pass.

    A category-scoped request cannot trust Telegram's own count here: any
    get_messages(filter=InputMessagesFilterVideo) call is routed by Telethon
    through messages.SearchRequest with min_id/max_id hardcoded to 0
    server-side (verified against the installed Telethon source,
    .venv/Lib/site-packages/telethon/client/messages.py) -- cat_start/
    cat_end never reach Telegram, so the returned count is the whole
    channel's video total, not the category's. _get_category_count already
    computes a per-category count from postData1 for the /api/videos route
    (exact, or -- per categories.get_categories()'s counts_exact flag --
    estimated if Mongo was unreachable on the last refresh); reuse it here
    instead of trusting Telegram for a category-bounded request.
    """
    if category is not None:
        total = await _get_category_count(category)
        category_data = await categories.get_categories()
        return total, category_data["counts_exact"]

    # A single limit=1 listing carries Telethon's exact match count for the
    # whole channel, so the honest total is known without paging thousands
    # of ids up front; the ids themselves are then paged lazily during
    # worker ticks, exactly like the prewarm tier, so this request never
    # blocks on them.
    result = await telegram.list_videos_with_total(
        limit=1, cat_start=cat_start, cat_end=cat_end
    )
    if result is None:
        return None, True
    _, total = result
    return total or 0, True


def _build_batch_message(queued: int, total: int, counts_exact: bool) -> str:
    """Build the batch-start progress message, honest about an estimated total."""
    qualifier = "" if counts_exact else "approximately "
    if total > prefetch.BATCH_ENQUEUE_CAP:
        return (
            f"Downloading the first {queued} of {qualifier}{total} videos to cache "
            f"(capped at {prefetch.BATCH_ENQUEUE_CAP})"
        )
    return f"Downloading {qualifier}{queued} videos to cache"


@app.delete("/api/prefetch/batch", dependencies=[Depends(require_auth)])
async def stop_batch_download():
    prefetch.clear_batch()
    return {"success": True, "message": "Batch download cancelled"}


@app.post("/api/cache/clear", dependencies=[Depends(require_auth)])
async def clear_cache():
    await prefetch.stop()
    try:
        await asyncio.to_thread(settings.delete_cache_tree, cache.CACHE_ROOT)
        cache.reset_accounting()
        return {"success": True, "message": "Cache cleared"}
    finally:
        await prefetch.start()


@app.post("/api/cache/settings", dependencies=[Depends(require_auth)])
async def set_cache_settings(body: CacheSettingsBody):
    if (
        body.cache_dir is None
        and body.cache_max_gb is None
        and body.tg_connections is None
    ):
        return {"success": False, "message": "Nothing to change"}

    if body.cache_dir is not None:
        result = await apply_dir_change(body.cache_dir)
        if not result["success"]:
            return format_settings_result(result)

    if body.cache_max_gb is not None:
        result = await settings.apply_max_gb(body.cache_max_gb)
        if not result["success"]:
            return format_settings_result(result)

    if body.tg_connections is not None:
        result = await settings.apply_tg_connections(body.tg_connections)

    return format_settings_result(result)


async def apply_dir_change(cache_dir: str) -> dict:
    await prefetch.stop()
    try:
        result = await settings.change_cache_dir(cache_dir)
    finally:
        await prefetch.start()

    if result["success"] and result.get("changed"):
        asyncio.create_task(
            asyncio.to_thread(settings.cleanup_old_root, result["old_root"])
        )
    return result


def format_settings_result(result: dict) -> dict:
    return {"success": result["success"], "message": result["message"]}


# --- channels ---


@app.get("/api/channels", dependencies=[Depends(require_auth)])
async def get_channels():
    return {"channels": await channels.list_channels()}


@app.post("/api/channels", dependencies=[Depends(require_auth)])
async def add_channel(body: ChannelBody):
    return await channels.add_channel(body.channel.strip())


@app.post("/api/channels/default", dependencies=[Depends(require_auth)])
async def set_default_channel(body: ChannelIdBody):
    return await channels.set_default(body.id)


@app.post("/api/channels/active", dependencies=[Depends(require_auth)])
async def activate_channel(body: ChannelIdBody):
    await prefetch.stop()
    try:
        telegram.clear_messages()
        old_key = channels.active_key()
        result = await channels.set_active(body.id)
        if not result["success"]:
            return result
        cache.reset_accounting()
        await wipe_channel_cache(old_key)
        return result
    finally:
        await prefetch.start()


@app.delete("/api/channels/{channel_id}", dependencies=[Depends(require_auth)])
async def remove_channel(channel_id: str):
    return await channels.remove_channel(channel_id)


async def wipe_channel_cache(channel_key: str | None) -> None:
    if channel_key is None:
        return
    try:
        await asyncio.gather(
            asyncio.to_thread(
                shutil.rmtree,
                cache.CACHE_ROOT / "blocks" / channel_key,
                ignore_errors=True,
            ),
            asyncio.to_thread(
                shutil.rmtree,
                cache.CACHE_ROOT / "thumbs" / channel_key,
                ignore_errors=True,
            ),
        )
    except Exception:
        logger.exception("Failed to wipe cache for channel %r", channel_key)


# --- videos ---


@app.get("/api/categories", dependencies=[Depends(require_auth)])
async def get_categories():
    channel_key = channels.active_key()
    if channel_key != categories.STUFF_CHANNEL:
        return {
            "channel": channel_key,
            "counts_exact": True,
            "categories": [],
        }
    category_data = await categories.get_categories()
    return {"channel": channel_key, **category_data}


def parse_range(range_header: str, file_size: int):
    """
    Parse a single-range 'bytes=START-END' header.
    Returns (start, end) inclusive, or None if unsatisfiable.
    """
    if not range_header or not range_header.startswith("bytes="):
        return 0, file_size - 1

    spec = range_header[len("bytes="):].split(",")[0].strip()
    start_str, _, end_str = spec.partition("-")

    try:
        if start_str == "":
            # suffix range: bytes=-N -> last N bytes
            length = int(end_str)
            if length <= 0:
                return None
            start = max(0, file_size - length)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
    except ValueError:
        return None

    if start < 0 or start >= file_size or end < start:
        return None
    end = min(end, file_size - 1)
    return start, end


@app.get("/api/videos", dependencies=[Depends(require_auth)])
async def videos(
    limit: int = Query(default=50, ge=0),
    before_id: int | None = None,
    after_id: int | None = None,
    offset: int = Query(default=0, ge=0),
    category: str | None = None,
    search: str | None = None,
    sort: Literal["asc", "desc"] = "asc",
):
    """List videos, or (when `search` is set) text-search captions instead —
    sort/before_id/after_id/category are all ignored in search mode.

    `sort=asc` (default) lists oldest-first and pages forward with
    `after_id`. `sort=desc` lists newest-first (the original behavior) and
    pages with `before_id`, unchanged. Passing the other direction's cursor
    is rejected rather than silently mishandled."""
    search = search.strip() if search else None
    if search:
        result = await video_metadata.search_videos(search, limit, offset)
        if result is None:
            raise HTTPException(status_code=502, detail="Search request failed")
        video_items, total, next_offset = result
        return {"videos": video_items, "total": total, "next_offset": next_offset}

    _reject_mismatched_cursor(sort, before_id, after_id)
    category_bounds = await _resolve_category(category)
    cat_start, cat_end = category_bounds if category_bounds else (None, None)
    result = await telegram.list_videos_with_total(
        limit=limit,
        before_id=before_id,
        after_id=after_id,
        offset=offset,
        cat_start=cat_start,
        cat_end=cat_end,
        reverse=(sort == "asc"),
    )
    if result is None:
        raise HTTPException(status_code=502, detail="Telegram request failed")
    video_items, total = result
    video_items = await video_metadata.enrich_videos(video_items)
    await playability.enrich_playability(video_items)
    if category is not None:
        total = await _get_category_count(category)
    return {"videos": video_items, "total": total}


def _reject_mismatched_cursor(
    sort: str, before_id: int | None, after_id: int | None
) -> None:
    if sort == "asc" and before_id is not None:
        raise HTTPException(
            status_code=400,
            detail="before_id pages sort=desc; use after_id with sort=asc",
        )
    if sort == "desc" and after_id is not None:
        raise HTTPException(
            status_code=400,
            detail="after_id pages sort=asc; use before_id with sort=desc",
        )


async def _resolve_category(
    category: str | None,
) -> tuple[int, int | None] | None:
    if category is None:
        return None
    if channels.active_key() != categories.STUFF_CHANNEL:
        raise HTTPException(
            status_code=400,
            detail="categories unavailable for this channel",
        )
    # Marker-derived keys exist only after a refresh, so load before resolving.
    await categories.ensure_fresh()
    bounds = categories.resolve(category)
    if bounds is None:
        raise HTTPException(status_code=404, detail="unknown category")
    return bounds


async def _get_category_count(category_key: str) -> int:
    category_data = await categories.get_categories()
    for category in category_data["categories"]:
        if category["key"] == category_key:
            return category["count"]
        for sub in category["subs"]:
            if sub["key"] == category_key:
                return sub["count"]
    raise HTTPException(status_code=404, detail="unknown category")


@app.get("/stream/{msg_id}", dependencies=[Depends(require_auth)])
async def stream(msg_id: int, request: Request, preview: bool = False):
    channel_key = channels.active_key()
    if channel_key is None:
        raise HTTPException(status_code=404, detail="No active channel")
    msg = await telegram.get_message(msg_id, channel_key)
    if not msg or not msg.file:
        raise HTTPException(status_code=404, detail="Message not found")

    file_size = msg.file.size
    mime = msg.file.mime_type or "video/mp4"
    range_header = request.headers.get("Range")

    parsed = parse_range(range_header, file_size)
    if parsed is None:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    start, end = parsed
    content_length = end - start + 1

    status = 206 if range_header else 200
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
    }
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    return StreamingResponse(
        streaming.stream_range(channel_key, msg, start, end, preview=preview),
        status_code=status,
        media_type=mime,
        headers=headers,
    )


@app.get("/thumb/{msg_id}", dependencies=[Depends(require_auth)])
async def thumb(msg_id: int):
    channel_key = channels.active_key()
    if channel_key is None:
        raise HTTPException(status_code=404, detail="No active channel")
    cached = cache.read_thumb(channel_key, msg_id)
    if cached:
        return build_thumb_response(cached)

    msg = await telegram.get_message(msg_id, channel_key)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    data = await telegram.get_thumbnail(msg)
    if not data:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    cache.write_thumb(channel_key, msg_id, data)
    return build_thumb_response(data)


def build_thumb_response(data: bytes) -> Response:
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", port=BACKEND_PORT, reload=True)
