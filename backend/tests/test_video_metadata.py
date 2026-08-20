"""Unit tests for video_metadata: caption/posted_ts enrichment for /api/videos."""

from types import SimpleNamespace

import pytest

import categories
import channels
import db
import telegram
import video_metadata


@pytest.fixture(autouse=True)
def stuff_channel_active(monkeypatch):
    """search_videos only has postData1 data for the Stuff channel; default
    every test here to it so existing search coverage keeps exercising the
    Mongo/Telegram paths, and the wrong-channel test overrides this itself."""
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


class FakeSearchCursor:
    """Chainable cursor stand-in for find().sort().skip().limit().to_list()."""

    def __init__(self, documents):
        self.documents = documents
        self.sort_spec = None
        self.skip_n = None
        self.limit_n = None

    def sort(self, spec):
        self.sort_spec = spec
        return self

    def skip(self, n):
        self.skip_n = n
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    async def to_list(self, length):
        return self.documents


class FakeSearchCollection:
    """Stands in for db.postdata_collection() across BOTH the text-search
    find() and (when search_videos goes on to enrich) the caption find() —
    each find() call is recorded in `cursors` since the second overwrites
    single-cursor state before a test can inspect the first."""

    def __init__(self, documents=None, total=0, find_error=None, count_error=None):
        self.documents = documents or []
        self.total = total
        self.find_error = find_error
        self.count_error = count_error
        self.seen_filter = None
        self.seen_projection = None
        self.cursor = None
        self.cursors = []
        self.count_calls = 0

    def find(self, query, projection):
        self.seen_filter = query
        self.seen_projection = projection
        if self.find_error:
            raise self.find_error
        self.cursor = FakeSearchCursor(self.documents)
        self.cursors.append(self.cursor)
        return self.cursor

    async def count_documents(self, query):
        self.count_calls += 1
        if self.count_error:
            raise self.count_error
        return self.total


def make_video_msg(msg_id, name):
    return SimpleNamespace(
        id=msg_id,
        date=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        file=SimpleNamespace(
            name=name, size=100, mime_type="video/mp4",
            width=None, height=None, duration=None,
        ),
    )


# --- fetch_captions_for_ids ---


async def test_fetch_captions_returns_map_keyed_by_message_id(monkeypatch):
    collection = FakeCollection([
        {"forwardFromMessageId": 7, "caption": "A caption", "datePosted": 1000},
    ])
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    result = await video_metadata.fetch_captions_for_ids([7, 8])

    assert result == {
        7: {"forwardFromMessageId": 7, "caption": "A caption", "datePosted": 1000}
    }
    assert collection.seen_query == {
        "paramType": "vidParams",
        "forwardFromMessageId": {"$in": [7, 8]},
    }
    assert collection.seen_projection == {
        "forwardFromMessageId": 1,
        "caption": 1,
        "datePosted": 1,
        "_id": 0,
    }


async def test_fetch_captions_omits_ids_with_no_matching_document(monkeypatch):
    # id 8 has no vidParams doc in postData1, so Mongo simply never returns it.
    collection = FakeCollection([
        {"forwardFromMessageId": 7, "caption": "A caption", "datePosted": 1000},
    ])
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    result = await video_metadata.fetch_captions_for_ids([7, 8])

    assert 8 not in result


async def test_fetch_captions_returns_empty_dict_on_mongo_failure(monkeypatch):
    collection = FakeCollection(error=RuntimeError("offline"))
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    result = await video_metadata.fetch_captions_for_ids([7])

    assert result == {}


async def test_fetch_captions_returns_empty_dict_when_mongo_not_connected(monkeypatch):
    # db.postdata_collection() itself raises when _client is None.
    monkeypatch.setattr(db, "_client", None)

    result = await video_metadata.fetch_captions_for_ids([7])

    assert result == {}


async def test_fetch_captions_skips_mongo_for_empty_ids(monkeypatch):
    def fail():
        raise AssertionError("must not query Mongo for an empty id list")

    monkeypatch.setattr(db, "postdata_collection", fail)

    assert await video_metadata.fetch_captions_for_ids([]) == {}


# --- merge_captions ---


def test_merge_captions_fills_hit_and_miss():
    videos = [{"id": 7, "name": "a.mp4"}, {"id": 8, "name": "b.mp4"}]
    captions_by_id = {7: {"caption": "Title", "datePosted": 1700000000}}

    result = video_metadata.merge_captions(videos, captions_by_id)

    assert result == [
        {"id": 7, "name": "a.mp4", "caption": "Title", "posted_ts": 1700000000},
        {"id": 8, "name": "b.mp4", "caption": None, "posted_ts": None},
    ]


def test_merge_captions_does_not_mutate_input_videos():
    videos = [{"id": 7, "name": "a.mp4"}]

    video_metadata.merge_captions(videos, {7: {"caption": "Title", "datePosted": 1}})

    assert videos == [{"id": 7, "name": "a.mp4"}]


def test_merge_captions_rejects_wrong_typed_fields():
    videos = [{"id": 7}]
    captions_by_id = {7: {"caption": 123, "datePosted": "not-a-number"}}

    result = video_metadata.merge_captions(videos, captions_by_id)

    assert result == [{"id": 7, "caption": None, "posted_ts": None}]


# --- enrich_videos ---


async def test_enrich_videos_skips_mongo_for_empty_page(monkeypatch):
    def fail(ids):
        raise AssertionError("must not query Mongo for an empty video page")

    monkeypatch.setattr(video_metadata, "fetch_captions_for_ids", fail)

    assert await video_metadata.enrich_videos([]) == []


async def test_enrich_videos_merges_fetched_captions(monkeypatch):
    async def fake_fetch(ids):
        assert ids == [7]
        return {7: {"caption": "Title", "datePosted": 5}}

    monkeypatch.setattr(video_metadata, "fetch_captions_for_ids", fake_fetch)

    result = await video_metadata.enrich_videos([{"id": 7}])

    assert result == [{"id": 7, "caption": "Title", "posted_ts": 5}]


async def test_enrich_videos_falls_back_to_nulls_on_mongo_failure(monkeypatch):
    collection = FakeCollection(error=RuntimeError("offline"))
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    result = await video_metadata.enrich_videos([{"id": 7}])

    assert result == [{"id": 7, "caption": None, "posted_ts": None}]


# --- GET /api/videos response shape ---


def test_videos_route_merges_caption_and_posted_ts(authed_client, monkeypatch):
    async def fake_list_videos_with_total(limit=50, before_id=None, offset=0):
        return [{"id": 7, "name": "a.mp4"}, {"id": 8, "name": "b.mp4"}], 2

    collection = FakeCollection([
        {"forwardFromMessageId": 7, "caption": "A caption", "datePosted": 1700000000},
    ])

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos_with_total)
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    response = authed_client.get("/api/videos")

    assert response.status_code == 200
    assert response.json() == {
        "videos": [
            {"id": 7, "name": "a.mp4", "caption": "A caption", "posted_ts": 1700000000},
            {"id": 8, "name": "b.mp4", "caption": None, "posted_ts": None},
        ],
        "total": 2,
    }


def test_videos_route_degrades_to_nulls_on_mongo_failure(authed_client, monkeypatch):
    async def fake_list_videos_with_total(limit=50, before_id=None, offset=0):
        return [{"id": 7, "name": "a.mp4"}], 1

    collection = FakeCollection(error=RuntimeError("offline"))

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos_with_total)
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    response = authed_client.get("/api/videos")

    assert response.status_code == 200
    assert response.json() == {
        "videos": [{"id": 7, "name": "a.mp4", "caption": None, "posted_ts": None}],
        "total": 1,
    }


# --- find_matching_ids ---


async def test_find_matching_ids_builds_relevance_ranked_query(monkeypatch):
    collection = FakeSearchCollection(
        documents=[
            {"forwardFromMessageId": 9},
            {"forwardFromMessageId": 7},
        ],
        total=5,
    )
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    ids, total, matched_count = await video_metadata.find_matching_ids("alice", limit=10, offset=20)

    assert ids == [9, 7]
    assert total == 5
    assert matched_count == 2
    assert collection.seen_filter == {
        "$text": {"$search": "alice"},
        "paramType": "vidParams",
    }
    assert collection.seen_projection == {
        "forwardFromMessageId": 1,
        "score": {"$meta": "textScore"},
        "_id": 0,
    }
    assert collection.cursor.sort_spec == [("score", {"$meta": "textScore"})]
    assert collection.cursor.skip_n == 20
    assert collection.cursor.limit_n == 10
    assert collection.count_calls == 1


async def test_find_matching_ids_skips_documents_missing_valid_id(monkeypatch):
    # matched_count still counts both raw documents even though neither
    # yields a valid id — next_offset is based on documents consumed, not
    # on len(ids).
    collection = FakeSearchCollection(documents=[{"forwardFromMessageId": None}, {}])
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    ids, _, matched_count = await video_metadata.find_matching_ids("x", limit=10, offset=0)

    assert ids == []
    assert matched_count == 2


async def test_find_matching_ids_returns_none_on_find_failure(monkeypatch):
    collection = FakeSearchCollection(find_error=RuntimeError("offline"))
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    result = await video_metadata.find_matching_ids("alice", limit=10, offset=0)

    assert result is None


async def test_find_matching_ids_returns_none_on_count_failure(monkeypatch):
    collection = FakeSearchCollection(
        documents=[{"forwardFromMessageId": 7}], count_error=RuntimeError("offline")
    )
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    result = await video_metadata.find_matching_ids("alice", limit=10, offset=0)

    assert result is None


async def test_find_matching_ids_limit_zero_skips_find_and_returns_count_only(monkeypatch):
    # pymongo's cursor.limit(0) means "no limit", not zero — limit=0 must
    # skip find/sort/skip entirely and report only the true match count.
    collection = FakeSearchCollection(total=7)
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    ids, total, matched_count = await video_metadata.find_matching_ids("alice", limit=0, offset=0)

    assert (ids, total, matched_count) == ([], 7, 0)
    assert collection.cursors == []  # find()/sort()/skip()/limit() never called
    assert collection.count_calls == 1


async def test_find_matching_ids_limit_zero_returns_none_on_count_failure(monkeypatch):
    collection = FakeSearchCollection(count_error=RuntimeError("offline"))
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    result = await video_metadata.find_matching_ids("alice", limit=0, offset=0)

    assert result is None


# --- search_videos ---


async def test_search_videos_returns_empty_page_for_non_stuff_channel(monkeypatch):
    # postData1 only has caption data for the Stuff channel — search on any
    # other active channel must short-circuit before touching Mongo or
    # Telegram at all (numeric msg_id collisions would otherwise return
    # unrelated videos from the wrong channel).
    monkeypatch.setattr(channels, "active_key", lambda: "-1009999999999")

    def fail_mongo():
        raise AssertionError("must not query Mongo for a non-Stuff channel")

    def fail_telegram(ids):
        raise AssertionError("must not resolve Telegram messages for a non-Stuff channel")

    monkeypatch.setattr(db, "postdata_collection", fail_mongo)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fail_telegram)

    video_items, total, next_offset = await video_metadata.search_videos("alice", limit=10, offset=7)

    assert (video_items, total) == ([], 0)
    assert next_offset == 7  # non-Stuff-channel path: next_offset == offset


async def test_search_videos_preserves_relevance_order_and_matches_normal_shape(monkeypatch):
    async def fake_find_matching_ids(search, limit, offset):
        assert (search, limit, offset) == ("alice", 10, 0)
        # matched_count (3) is the raw Mongo doc count consumed for this
        # page — it stays 3 even though Telegram only resolves 2 of the ids.
        return [9, 7, 8], 3, 3

    async def fake_get_messages_by_ids_or_raise(ids):
        assert ids == [9, 7, 8]
        # id 8 doesn't resolve (deleted/non-video) — Telethon's None dropped upstream.
        return [make_video_msg(9, "nine.mp4"), make_video_msg(7, "seven.mp4")]

    collection = FakeCollection([
        {"forwardFromMessageId": 9, "caption": "Nine", "datePosted": 900},
        {"forwardFromMessageId": 7, "caption": "Seven", "datePosted": 700},
    ])

    monkeypatch.setattr(video_metadata, "find_matching_ids", fake_find_matching_ids)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fake_get_messages_by_ids_or_raise)
    monkeypatch.setattr(db, "postdata_collection", lambda: collection)

    video_items, total, next_offset = await video_metadata.search_videos("alice", limit=10, offset=0)

    assert total == 3
    assert [item["id"] for item in video_items] == [9, 7]
    # next_offset tracks the 3 raw matches consumed, not the 2 videos Telegram resolved.
    assert next_offset == 3
    assert video_items[0] == {
        "id": 9,
        "date": "2026-01-01T00:00:00",
        "name": "nine.mp4",
        "size": 100,
        "mime": "video/mp4",
        "width": None,
        "height": None,
        "duration": None,
        "caption": "Nine",
        "posted_ts": 900,
    }


async def test_search_videos_returns_empty_page_without_calling_telegram(monkeypatch):
    async def fake_find_matching_ids(search, limit, offset):
        return [], 0, 0

    def fail(ids):
        raise AssertionError("must not resolve Telegram messages for zero matches")

    monkeypatch.setattr(video_metadata, "find_matching_ids", fake_find_matching_ids)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fail)

    video_items, total, next_offset = await video_metadata.search_videos("nothing", limit=10, offset=12)

    assert (video_items, total) == ([], 0)
    assert next_offset == 12  # zero matches consumed: next_offset == offset


async def test_search_videos_returns_none_on_mongo_failure(monkeypatch):
    collection = FakeSearchCollection(find_error=RuntimeError("offline"))

    def fail(ids):
        raise AssertionError("must not resolve Telegram messages when search itself failed")

    monkeypatch.setattr(db, "postdata_collection", lambda: collection)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fail)

    result = await video_metadata.search_videos("alice", limit=10, offset=0)

    assert result is None


async def test_search_videos_returns_none_when_telegram_resolution_raises(monkeypatch):
    async def fake_find_matching_ids(search, limit, offset):
        return [7], 1, 1

    def fail(ids):
        raise RuntimeError("telegram unreachable")

    monkeypatch.setattr(video_metadata, "find_matching_ids", fake_find_matching_ids)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fail)

    result = await video_metadata.search_videos("alice", limit=10, offset=0)

    assert result is None


async def test_search_videos_limit_zero_returns_next_offset_equal_to_offset(monkeypatch):
    # limit=0 is the count-only path: no documents are consumed, so
    # next_offset must equal offset regardless of the total match count.
    collection = FakeSearchCollection(total=99)

    def fail(ids):
        raise AssertionError("must not resolve Telegram messages for a count-only page")

    monkeypatch.setattr(db, "postdata_collection", lambda: collection)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fail)

    video_items, total, next_offset = await video_metadata.search_videos("alice", limit=0, offset=25)

    assert (video_items, total) == ([], 99)
    assert next_offset == 25


async def test_search_videos_forwards_offset_pagination(monkeypatch):
    # Same fake collection serves both the text-search find() and the
    # enrichment find() — its fixed documents have no caption/datePosted, so
    # enrichment naturally resolves to null without a second fake.
    collection = FakeSearchCollection(documents=[{"forwardFromMessageId": 7}], total=42)

    async def fake_get_messages_by_ids_or_raise(ids):
        return [make_video_msg(7, "seven.mp4")]

    monkeypatch.setattr(db, "postdata_collection", lambda: collection)
    monkeypatch.setattr(telegram, "get_messages_by_ids_or_raise", fake_get_messages_by_ids_or_raise)

    video_items, total, next_offset = await video_metadata.search_videos("alice", limit=15, offset=30)

    search_cursor = collection.cursors[0]
    assert search_cursor.skip_n == 30
    assert search_cursor.limit_n == 15
    assert total == 42
    assert next_offset == 31  # offset(30) + 1 raw match document consumed
    assert [item["id"] for item in video_items] == [7]
    assert video_items[0]["caption"] is None
    assert video_items[0]["posted_ts"] is None


# --- GET /api/videos search mode ---


def test_videos_route_search_mode_matches_normal_response_shape(authed_client, monkeypatch):
    seen = {}

    async def fake_search_videos(search, limit, offset):
        seen["args"] = (search, limit, offset)
        return [{"id": 7, "name": "a.mp4", "caption": "A caption", "posted_ts": 5}], 1, 15

    monkeypatch.setattr(video_metadata, "search_videos", fake_search_videos)

    response = authed_client.get("/api/videos?search=alice&limit=10&offset=5")

    assert response.status_code == 200
    assert seen["args"] == ("alice", 10, 5)
    assert response.json() == {
        "videos": [{"id": 7, "name": "a.mp4", "caption": "A caption", "posted_ts": 5}],
        "total": 1,
        "next_offset": 15,
    }


def test_videos_route_search_ignores_before_id_and_category(authed_client, monkeypatch):
    async def fake_search_videos(search, limit, offset):
        return [], 0, offset

    def fail(*args, **kwargs):
        raise AssertionError("search mode must not touch the Telegram message cursor")

    monkeypatch.setattr(video_metadata, "search_videos", fake_search_videos)
    monkeypatch.setattr(telegram, "list_videos_with_total", fail)

    response = authed_client.get("/api/videos?search=alice&before_id=999&category=bar")

    assert response.status_code == 200
    assert response.json() == {"videos": [], "total": 0, "next_offset": 0}


def test_videos_route_blank_search_falls_back_to_normal_listing(authed_client, monkeypatch):
    def fail(search, limit, offset):
        raise AssertionError("blank search must not enter search mode")

    async def fake_list_videos_with_total(limit=50, before_id=None, offset=0):
        return [], 0

    monkeypatch.setattr(video_metadata, "search_videos", fail)
    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos_with_total)

    response = authed_client.get("/api/videos?search=%20")

    assert response.status_code == 200
    assert response.json() == {"videos": [], "total": 0}


def test_videos_route_without_search_param_is_untouched(authed_client, monkeypatch):
    def fail(search, limit, offset):
        raise AssertionError("no-search requests must never call search_videos")

    async def fake_list_videos_with_total(limit=50, before_id=None, offset=0):
        return [{"id": 3, "name": "c.mp4"}], 1

    monkeypatch.setattr(video_metadata, "search_videos", fail)
    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list_videos_with_total)

    response = authed_client.get("/api/videos")

    assert response.status_code == 200
    assert response.json() == {
        "videos": [{"id": 3, "name": "c.mp4", "caption": None, "posted_ts": None}],
        "total": 1,
    }
