"""
Route-handler tests with the telegram module's functions monkeypatched.
Patching telegram.* attributes works because main.py does `import telegram`
and resolves telegram.xxx at call time.
"""

from types import SimpleNamespace

import cache
import streaming
import telegram

FILE_SIZE = 100
DATA = bytes(range(FILE_SIZE))


# --- GET /api/videos ---


def test_videos_returns_the_list_from_telegram(authed_client, monkeypatch):
    videos = [{"id": 7, "name": "a.mp4", "size": 123}]

    async def fake_list_videos(limit=50, before_id=None):
        return videos

    monkeypatch.setattr(telegram, "list_videos", fake_list_videos)
    resp = authed_client.get("/api/videos")
    assert resp.status_code == 200
    assert resp.json() == videos


def test_videos_returns_502_when_telegram_fails(authed_client, monkeypatch):
    async def fake_list_videos(limit=50, before_id=None):
        return None

    monkeypatch.setattr(telegram, "list_videos", fake_list_videos)
    assert authed_client.get("/api/videos").status_code == 502


def test_videos_passes_before_id_through(authed_client, monkeypatch):
    seen = {}

    async def fake_list_videos(limit=50, before_id=None):
        seen["limit"] = limit
        seen["before_id"] = before_id
        return []

    monkeypatch.setattr(telegram, "list_videos", fake_list_videos)
    resp = authed_client.get("/api/videos?limit=25&before_id=1234")

    assert resp.status_code == 200
    assert seen == {"limit": 25, "before_id": 1234}


# --- GET /stream/{msg_id} ---


def test_stream_missing_message_returns_404(authed_client, monkeypatch):
    install_get_message(monkeypatch, None)
    assert authed_client.get("/stream/1").status_code == 404


def test_stream_unsatisfiable_range_returns_416(authed_client, monkeypatch):
    install_get_message(monkeypatch, make_video_msg())
    resp = authed_client.get("/stream/1", headers={"Range": "bytes=999999-"})
    assert resp.status_code == 416
    assert resp.headers["Content-Range"] == f"bytes */{FILE_SIZE}"


def test_stream_valid_range_returns_206_with_exact_slice(authed_client, monkeypatch):
    install_get_message(monkeypatch, make_video_msg())
    install_fake_stream_range(monkeypatch)

    resp = authed_client.get("/stream/1", headers={"Range": "bytes=10-19"})

    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 10-19/{FILE_SIZE}"
    assert resp.headers["Content-Length"] == "10"
    assert resp.content == DATA[10:20]


def test_stream_without_range_returns_200_full_file(authed_client, monkeypatch):
    install_get_message(monkeypatch, make_video_msg())
    install_fake_stream_range(monkeypatch)

    resp = authed_client.get("/stream/1")

    assert resp.status_code == 200
    assert resp.headers["Content-Length"] == str(FILE_SIZE)
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.content == DATA


# --- GET /thumb/{msg_id} ---


def test_thumb_missing_message_returns_404(authed_client, monkeypatch):
    install_get_message(monkeypatch, None)
    assert authed_client.get("/thumb/1").status_code == 404


def test_thumb_without_thumbnail_returns_404(authed_client, monkeypatch):
    install_get_message(monkeypatch, make_video_msg())
    install_get_thumbnail(monkeypatch, None)
    assert authed_client.get("/thumb/1").status_code == 404


def test_thumb_success_returns_jpeg_bytes(authed_client, monkeypatch):
    fake_jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    install_get_message(monkeypatch, make_video_msg())
    install_get_thumbnail(monkeypatch, fake_jpeg)

    resp = authed_client.get("/thumb/1")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == fake_jpeg


def test_thumb_is_cached_on_disk_and_served_without_telegram(authed_client, monkeypatch, tmp_path):
    point_thumb_cache_at(tmp_path, monkeypatch)
    fake_jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    install_get_message(monkeypatch, make_video_msg())
    install_get_thumbnail(monkeypatch, fake_jpeg)

    first = authed_client.get("/thumb/1")
    install_get_thumbnail(monkeypatch, None)      # Telegram would now fail
    second = authed_client.get("/thumb/1")        # must come from disk

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.content == fake_jpeg


def test_thumb_sends_cache_control_header(authed_client, monkeypatch, tmp_path):
    point_thumb_cache_at(tmp_path, monkeypatch)
    install_get_message(monkeypatch, make_video_msg())
    install_get_thumbnail(monkeypatch, b"\xff\xd8jpeg")

    resp = authed_client.get("/thumb/1")

    assert resp.headers["Cache-Control"] == "private, max-age=86400"


# --- helpers ---


def point_thumb_cache_at(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)


def install_get_message(monkeypatch, msg):
    async def fake_get_message(msg_id):
        return msg

    monkeypatch.setattr(telegram, "get_message", fake_get_message)


def install_get_thumbnail(monkeypatch, data):
    async def fake_get_thumbnail(msg):
        return data

    monkeypatch.setattr(telegram, "get_thumbnail", fake_get_thumbnail)


def install_fake_stream_range(monkeypatch):
    async def fake_stream_range(msg, start, end):
        yield DATA[start:end + 1]

    monkeypatch.setattr(streaming, "stream_range", fake_stream_range)


def make_video_msg():
    return SimpleNamespace(
        id=1,
        file=SimpleNamespace(size=FILE_SIZE, mime_type="video/mp4"),
        media=object(),
    )
