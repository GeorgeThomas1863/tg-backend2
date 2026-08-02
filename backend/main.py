"""
FastAPI app. HTTP concerns only — Range parsing, status codes, headers.
All Telegram work is delegated to the telegram module.
"""

from contextlib import asynccontextmanager

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import cache
import downloader
import prefetch
import streaming
import telegram
from config import (
    AUTH_MAX_ATTEMPTS,
    AUTH_WINDOW_SECONDS,
    BACKEND_PORT,
    FRONTEND_ORIGIN,
    PW_HASH,
    SESSION_SECRET,
)
from rate_limit import AuthRateLimiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram.connect()
    await prefetch.start()
    yield
    await prefetch.stop()
    await downloader.disconnect_all()
    await telegram.disconnect()


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


class CachePausedBody(BaseModel):
    paused: bool


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


# --- cache ---


@app.get("/api/cache/status", dependencies=[Depends(require_auth)])
async def cache_status():
    worker = prefetch.status()
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
        "videos": cache.video_totals(),
    }


@app.post("/api/cache/paused", dependencies=[Depends(require_auth)])
async def set_cache_paused(body: CachePausedBody):
    prefetch.set_paused(body.paused)
    return {
        "success": True,
        "message": "Caching paused" if body.paused else "Caching resumed",
    }


# --- videos ---


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
async def videos(limit: int = 50, before_id: int | None = None):
    result = await telegram.list_videos(limit, before_id)
    if result is None:
        raise HTTPException(status_code=502, detail="Telegram request failed")
    return result


@app.get("/stream/{msg_id}", dependencies=[Depends(require_auth)])
async def stream(msg_id: int, request: Request):
    msg = await telegram.get_message(msg_id)
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
        streaming.stream_range(msg, start, end),
        status_code=status,
        media_type=mime,
        headers=headers,
    )


@app.get("/thumb/{msg_id}", dependencies=[Depends(require_auth)])
async def thumb(msg_id: int):
    cached = cache.read_thumb(msg_id)
    if cached:
        return build_thumb_response(cached)

    msg = await telegram.get_message(msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    data = await telegram.get_thumbnail(msg)
    if not data:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    cache.write_thumb(msg_id, data)
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
