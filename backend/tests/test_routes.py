"""
Route-handler tests with the telegram module's functions monkeypatched.
Patching telegram.* attributes works because main.py does `import telegram`
and resolves telegram.xxx at call time.
"""

from types import SimpleNamespace

import cache
import channels
import main
import prefetch
import pytest
import streaming
import telegram
import video_metadata

FILE_SIZE = 100
DATA = bytes(range(FILE_SIZE))


# --- /api/channels ---


def test_channels_list_endpoint(authed_client, monkeypatch):
    entries = [{"id": "one", "channel": "example"}]

    async def fake_list_channels():
        return entries

    monkeypatch.setattr(channels, "list_channels", fake_list_channels)

    response = authed_client.get("/api/channels")

    assert response.status_code == 200
    assert response.json() == {"channels": entries}


def test_add_channel_endpoint_trims_input(authed_client, monkeypatch):
    seen = []

    async def fake_add_channel(raw):
        seen.append(raw)
        return {"success": True, "message": "Channel added"}

    monkeypatch.setattr(channels, "add_channel", fake_add_channel)

    response = authed_client.post("/api/channels", json={"channel": " example "})

    assert response.json()["success"] is True
    assert seen == ["example"]


def test_set_default_channel_endpoint(authed_client, monkeypatch):
    async def fake_set_default(channel_id):
        return {"success": True, "message": channel_id}

    monkeypatch.setattr(channels, "set_default", fake_set_default)

    response = authed_client.post("/api/channels/default", json={"id": "two"})

    assert response.json() == {"success": True, "message": "two"}


def test_delete_channel_endpoint_preserves_registry_guards(authed_client, monkeypatch):
    async def fake_remove_channel(channel_id):
        return {
            "success": False,
            "message": f"The {channel_id} channel cannot be removed",
        }

    monkeypatch.setattr(channels, "remove_channel", fake_remove_channel)

    active = authed_client.delete("/api/channels/active")
    default = authed_client.delete("/api/channels/default")

    assert active.json()["success"] is False
    assert "active" in active.json()["message"]
    assert default.json()["success"] is False
    assert "default" in default.json()["message"]


def test_activate_channel_runs_switch_steps_in_order(authed_client, monkeypatch):
    steps = []

    async def fake_stop():
        steps.append("stop")

    async def fake_set_active(channel_id):
        steps.append(f"activate:{channel_id}")
        return {"success": True, "message": "Active channel updated"}

    async def fake_wipe(channel_key):
        steps.append(f"wipe:{channel_key}")

    async def fake_start():
        steps.append("start")

    monkeypatch.setattr(prefetch, "stop", fake_stop)
    monkeypatch.setattr(telegram, "clear_messages", lambda: steps.append("clear"))
    monkeypatch.setattr(channels, "active_key", lambda: "old")
    monkeypatch.setattr(channels, "set_active", fake_set_active)
    monkeypatch.setattr(cache, "reset_accounting", lambda: steps.append("reset"))
    monkeypatch.setattr(main, "wipe_channel_cache", fake_wipe)
    monkeypatch.setattr(prefetch, "start", fake_start)

    response = authed_client.post("/api/channels/active", json={"id": "two"})

    assert response.json()["success"] is True
    assert steps == [
        "stop",
        "clear",
        "activate:two",
        "reset",
        "wipe:old",
        "start",
    ]


@pytest.mark.parametrize("failure_step", ["set_active", "reset", "wipe"])
async def test_activate_channel_restarts_worker_after_exception(monkeypatch, failure_step):
    starts = []

    async def fake_stop():
        return None

    async def fake_set_active(channel_id):
        if failure_step == "set_active":
            raise RuntimeError("set active failed")
        return {"success": True, "message": "updated"}

    def fake_reset():
        if failure_step == "reset":
            raise RuntimeError("reset failed")

    async def fake_wipe(channel_key):
        if failure_step == "wipe":
            raise RuntimeError("wipe failed")

    async def fake_start():
        starts.append(True)

    monkeypatch.setattr(prefetch, "stop", fake_stop)
    monkeypatch.setattr(telegram, "clear_messages", lambda: None)
    monkeypatch.setattr(channels, "active_key", lambda: "old")
    monkeypatch.setattr(channels, "set_active", fake_set_active)
    monkeypatch.setattr(cache, "reset_accounting", fake_reset)
    monkeypatch.setattr(main, "wipe_channel_cache", fake_wipe)
    monkeypatch.setattr(prefetch, "start", fake_start)

    with pytest.raises(RuntimeError):
        await main.activate_channel(main.ChannelIdBody(id="two"))

    assert starts == [True]


# --- GET /api/videos ---


def test_videos_returns_envelope_from_telegram(authed_client, monkeypatch):
    videos = [{"id": 7, "name": "a.mp4", "size": 123}]

    async def fake_list_videos_with_total(**kwargs):
        return videos, 19

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos_with_total)
    resp = authed_client.get("/api/videos")
    assert resp.status_code == 200
    assert resp.json() == {
        "videos": [{"id": 7, "name": "a.mp4", "size": 123, "caption": None, "posted_ts": None}],
        "total": 19,
    }


def test_videos_returns_502_when_telegram_fails(authed_client, monkeypatch):
    async def fake_list_videos(**kwargs):
        return None

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos)
    assert authed_client.get("/api/videos").status_code == 502


def test_videos_search_returns_502_when_search_videos_fails(authed_client, monkeypatch):
    async def fake_search_videos(search, limit, offset):
        return None

    monkeypatch.setattr(video_metadata, "search_videos", fake_search_videos)
    resp = authed_client.get("/api/videos?search=alice")
    assert resp.status_code == 502


def test_videos_search_success_includes_next_offset(authed_client, monkeypatch):
    async def fake_search_videos(search, limit, offset):
        return [{"id": 7, "name": "a.mp4"}], 1, 9

    monkeypatch.setattr(video_metadata, "search_videos", fake_search_videos)
    resp = authed_client.get("/api/videos?search=alice&offset=5")

    assert resp.status_code == 200
    assert resp.json() == {
        "videos": [{"id": 7, "name": "a.mp4"}],
        "total": 1,
        "next_offset": 9,
    }


def test_videos_passes_before_id_through(authed_client, monkeypatch):
    seen = {}

    async def fake_list_videos(**kwargs):
        seen.update(kwargs)
        return [], 0

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos)
    # before_id pages sort=desc, so it must be paired with sort=desc now
    # that sort defaults to asc (item 2's oldest-first default).
    resp = authed_client.get("/api/videos?limit=25&before_id=1234&offset=8&sort=desc")

    assert resp.status_code == 200
    assert seen == {
        "limit": 25,
        "before_id": 1234,
        "after_id": None,
        "offset": 8,
        "cat_start": None,
        "cat_end": None,
        "reverse": False,
    }


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
    async def fake_get_message(msg_id, channel_key=None):
        return msg

    monkeypatch.setattr(telegram, "get_message", fake_get_message)


def install_get_thumbnail(monkeypatch, data):
    async def fake_get_thumbnail(msg):
        return data

    monkeypatch.setattr(telegram, "get_thumbnail", fake_get_thumbnail)


def install_fake_stream_range(monkeypatch):
    async def fake_stream_range(channel_key, msg, start, end, preview=False):
        yield DATA[start:end + 1]

    monkeypatch.setattr(streaming, "stream_range", fake_stream_range)


def make_video_msg():
    return SimpleNamespace(
        id=1,
        file=SimpleNamespace(size=FILE_SIZE, mime_type="video/mp4"),
        media=object(),
    )
