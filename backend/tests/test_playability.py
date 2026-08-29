"""Unit tests for playability: verdict enrichment for /api/videos."""

from types import SimpleNamespace

import pytest

import categories
import channels
import db
import playability
import telegram
import video_metadata


@pytest.fixture(autouse=True)
def stuff_channel_active(monkeypatch):
    """Playability verdicts only exist for the Stuff channel; default every
    test here to it, and the wrong-channel test overrides this itself."""
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, length):
        return self.documents


class FakeCollection:
    def __init__(self, documents=None, error=None):
        self.documents = documents or []
        self.error = error
        self.seen_query = None
        self.seen_projection = None

    def find(self, query, projection):
        self.seen_query = query
        self.seen_projection = projection
        if self.error:
            raise self.error
        return FakeCursor(self.documents)


def make_video_msg(msg_id, name):
    return SimpleNamespace(
        id=msg_id,
        date=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        file=SimpleNamespace(
            name=name, size=100, mime_type="video/mp4",
            width=None, height=None, duration=None,
        ),
    )


# --- fetch_verdicts_for_ids ---


async def test_fetch_verdicts_returns_map_keyed_by_message_id(monkeypatch):
    collection = FakeCollection([{"_id": 7, "verdict": "playable"}])
    monkeypatch.setattr(db, "playability_collection", lambda: collection)

    result = await playability.fetch_verdicts_for_ids([7, 8])

    assert result == {7: "playable"}
    assert collection.seen_query == {"_id": {"$in": [7, 8]}}
    assert collection.seen_projection == {"verdict": 1}


async def test_fetch_verdicts_omits_ids_with_no_matching_document(monkeypatch):
    # id 8 has no playability doc, so Mongo simply never returns it.
    collection = FakeCollection([{"_id": 7, "verdict": "playable"}])
    monkeypatch.setattr(db, "playability_collection", lambda: collection)

    result = await playability.fetch_verdicts_for_ids([7, 8])

    assert 8 not in result


async def test_fetch_verdicts_returns_empty_dict_on_mongo_failure(monkeypatch):
    collection = FakeCollection(error=RuntimeError("offline"))
    monkeypatch.setattr(db, "playability_collection", lambda: collection)

    result = await playability.fetch_verdicts_for_ids([7])

    assert result == {}


async def test_fetch_verdicts_returns_empty_dict_when_mongo_not_connected(monkeypatch):
    # db.playability_collection() itself raises when _client is None.
    monkeypatch.setattr(db, "_client", None)

    result = await playability.fetch_verdicts_for_ids([7])

    assert result == {}


async def test_fetch_verdicts_skips_mongo_for_empty_ids(monkeypatch):
    def fail():
        raise AssertionError("must not query Mongo for an empty id list")

    monkeypatch.setattr(db, "playability_collection", fail)

    assert await playability.fetch_verdicts_for_ids([]) == {}


# --- enrich_playability ---


async def test_enrich_playability_sets_verdict_when_present(monkeypatch):
    async def fake_fetch(ids):
        assert ids == [7]
        return {7: "playable"}

    monkeypatch.setattr(playability, "fetch_verdicts_for_ids", fake_fetch)

    videos = [{"id": 7}]
    await playability.enrich_playability(videos)

    assert videos == [{"id": 7, "playability": "playable"}]


async def test_enrich_playability_sets_none_when_id_missing(monkeypatch):
    async def fake_fetch(ids):
        return {}

    monkeypatch.setattr(playability, "fetch_verdicts_for_ids", fake_fetch)

    videos = [{"id": 7}, {"id": 8}]
    await playability.enrich_playability(videos)

    assert videos == [
        {"id": 7, "playability": None},
        {"id": 8, "playability": None},
    ]


async def test_enrich_playability_degrades_to_none_on_mongo_failure(monkeypatch):
    collection = FakeCollection(error=RuntimeError("offline"))
    monkeypatch.setattr(db, "playability_collection", lambda: collection)

    videos = [{"id": 7}]
    await playability.enrich_playability(videos)

    assert videos == [{"id": 7, "playability": None}]


async def test_enrich_playability_sets_none_and_skips_mongo_on_other_channel(monkeypatch):
    monkeypatch.setattr(channels, "active_key", lambda: "other")

    def fail(ids):
        raise AssertionError("must not query Mongo for a non-Stuff channel")

    monkeypatch.setattr(playability, "fetch_verdicts_for_ids", fail)

    videos = [{"id": 7}, {"id": 8}]
    await playability.enrich_playability(videos)

    assert videos == [
        {"id": 7, "playability": None},
        {"id": 8, "playability": None},
    ]


async def test_enrich_playability_skips_mongo_for_empty_page(monkeypatch):
    def fail(ids):
        raise AssertionError("must not query Mongo for an empty video page")

    monkeypatch.setattr(playability, "fetch_verdicts_for_ids", fail)

    videos = []
    await playability.enrich_playability(videos)

    assert videos == []


# --- GET /api/videos normal listing path ---


def test_videos_route_attaches_playability(authed_client, monkeypatch):
    async def fake_list_videos_with_total(**kwargs):
        return [{"id": 7, "name": "a.mp4"}, {"id": 8, "name": "b.mp4"}], 2

    caption_collection = FakeCollection([])
    verdict_collection = FakeCollection([{"_id": 7, "verdict": "playable"}])

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos_with_total)
    monkeypatch.setattr(db, "postdata_collection", lambda: caption_collection)
    monkeypatch.setattr(db, "playability_collection", lambda: verdict_collection)

    response = authed_client.get("/api/videos")

    assert response.status_code == 200
    body = response.json()
    assert body["videos"][0]["playability"] == "playable"
    assert body["videos"][1]["playability"] is None


def test_videos_route_playability_degrades_to_none_on_mongo_failure(authed_client, monkeypatch):
    async def fake_list_videos_with_total(**kwargs):
        return [{"id": 7, "name": "a.mp4"}], 1

    caption_collection = FakeCollection([])
    verdict_collection = FakeCollection(error=RuntimeError("offline"))

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos_with_total)
    monkeypatch.setattr(db, "postdata_collection", lambda: caption_collection)
    monkeypatch.setattr(db, "playability_collection", lambda: verdict_collection)

    response = authed_client.get("/api/videos")

    assert response.status_code == 200
    assert response.json()["videos"][0]["playability"] is None


# --- video_metadata.search_videos path ---


async def test_search_videos_attaches_playability(monkeypatch):
    async def fake_find_matching_ids(search, limit, offset):
        return [7], 1, 1

    async def fake_get_messages_by_ids_or_raise(ids):
        return [make_video_msg(7, "seven.mp4")]

    caption_collection = FakeCollection([])
    verdict_collection = FakeCollection([{"_id": 7, "verdict": "playable"}])

    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    monkeypatch.setattr(video_metadata, "find_matching_ids", fake_find_matching_ids)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fake_get_messages_by_ids_or_raise)
    monkeypatch.setattr(db, "postdata_collection", lambda: caption_collection)
    monkeypatch.setattr(db, "playability_collection", lambda: verdict_collection)

    video_items, total, next_offset = await video_metadata.search_videos("alice", limit=10, offset=0)

    assert video_items[0]["playability"] == "playable"


async def test_search_videos_playability_degrades_to_none_on_mongo_failure(monkeypatch):
    async def fake_find_matching_ids(search, limit, offset):
        return [7], 1, 1

    async def fake_get_messages_by_ids_or_raise(ids):
        return [make_video_msg(7, "seven.mp4")]

    caption_collection = FakeCollection([])
    verdict_collection = FakeCollection(error=RuntimeError("offline"))

    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    monkeypatch.setattr(video_metadata, "find_matching_ids", fake_find_matching_ids)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fake_get_messages_by_ids_or_raise)
    monkeypatch.setattr(db, "postdata_collection", lambda: caption_collection)
    monkeypatch.setattr(db, "playability_collection", lambda: verdict_collection)

    video_items, total, next_offset = await video_metadata.search_videos("alice", limit=10, offset=0)

    assert video_items[0]["playability"] is None


def test_videos_route_search_mode_attaches_playability(authed_client, monkeypatch):
    async def fake_find_matching_ids(search, limit, offset):
        return [7], 1, 1

    async def fake_get_messages_by_ids_or_raise(ids):
        return [make_video_msg(7, "seven.mp4")]

    caption_collection = FakeCollection([])
    verdict_collection = FakeCollection([{"_id": 7, "verdict": "playable"}])

    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    monkeypatch.setattr(video_metadata, "find_matching_ids", fake_find_matching_ids)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fake_get_messages_by_ids_or_raise)
    monkeypatch.setattr(db, "postdata_collection", lambda: caption_collection)
    monkeypatch.setattr(db, "playability_collection", lambda: verdict_collection)

    response = authed_client.get("/api/videos?search=alice")

    assert response.status_code == 200
    assert response.json()["videos"][0]["playability"] == "playable"
