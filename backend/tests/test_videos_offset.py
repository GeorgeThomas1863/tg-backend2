from datetime import datetime, timezone
from types import SimpleNamespace

import telegram


def make_message(message_id=7):
    file = SimpleNamespace(
        name="video.mp4",
        size=123,
        mime_type="video/mp4",
        width=1920,
        height=1080,
        duration=10,
    )
    return SimpleNamespace(
        id=message_id,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        file=file,
    )


class MessageResults(list):
    def __init__(self, messages, total):
        super().__init__(messages)
        self.total = total


async def test_list_videos_forwards_default_offset(monkeypatch):
    seen = {}

    async def fake_get_messages(channel, **kwargs):
        seen["channel"] = channel
        seen.update(kwargs)
        return MessageResults([], 0)

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    assert await telegram.list_videos() == []
    assert seen["add_offset"] == 0


async def test_total_and_offsets_come_from_same_search(monkeypatch):
    seen = {}

    async def fake_get_messages(channel, **kwargs):
        seen["channel"] = channel
        seen.update(kwargs)
        return MessageResults([make_message()], 42)

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    result = await telegram.list_videos_with_total(
        limit=12,
        before_id=900,
        offset=31,
    )

    assert result == ([telegram.media_to_dict(make_message())], 42)
    assert seen["limit"] == 12
    assert seen["offset_id"] == 900
    assert seen["add_offset"] == 31


async def test_total_degrades_to_none_when_result_lacks_total(monkeypatch):
    async def fake_get_messages(channel, **kwargs):
        return [make_message()]

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    videos, total = await telegram.list_videos_with_total()

    assert len(videos) == 1
    assert total is None


def test_route_uses_zero_offset_by_default(authed_client, monkeypatch):
    seen = {}

    async def fake_list_videos(limit=50, before_id=None, offset=0):
        seen["offset"] = offset
        return [], 5

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos)
    response = authed_client.get("/api/videos")

    assert response.status_code == 200
    assert response.json() == {"videos": [], "total": 5}
    assert seen["offset"] == 0


def test_route_rejects_negative_offset(authed_client):
    response = authed_client.get("/api/videos?offset=-1")

    assert response.status_code == 422


def test_route_rejects_negative_limit(authed_client):
    response = authed_client.get("/api/videos?limit=-1")

    assert response.status_code == 422
