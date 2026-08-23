import asyncio

import categories
import channels
import main
import telegram


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, length):
        return self.documents

    def sort(self, field, direction):
        assert field == "forwardFromMessageId"
        assert direction == 1
        self.documents.sort(key=lambda item: item["forwardFromMessageId"])
        return self


class FakeCollection:
    def __init__(self, ids=None, markers=None, error=None):
        self.ids = ids or []
        self.markers = markers or []
        self.error = error

    def find(self, query, projection):
        if self.error:
            raise self.error
        if query["paramType"] == "textParams":
            assert query["forwardFromMessageId"] == {
                "$gt": categories.HIGHEST_HARDCODED_ID
            }
            assert projection == {
                "_id": 0, "forwardFromMessageId": 1, "text": 1
            }
            return FakeCursor(self.markers.copy())
        assert query == {"paramType": "vidParams"}
        assert projection == {"_id": 0, "forwardFromMessageId": 1}
        documents = [{"forwardFromMessageId": value} for value in self.ids]
        return FakeCursor(documents)


def reset_counts(monkeypatch, collection):
    monkeypatch.setattr(categories, "_count_cache", None)
    monkeypatch.setattr(categories, "_count_cache_expires", 0.0)
    monkeypatch.setattr(categories, "get_collection", lambda: collection)
    monkeypatch.setattr(
        categories, "_table_state", (categories.CATEGORY_TABLE, categories._base_ranges)
    )


def test_keys_slug_tags_and_sub_names():
    old = categories.CATEGORY_TABLE[0]
    assert old["key"] == "old"
    assert old["subs"][2]["key"] == "old-naughty-america"


def test_nested_sub_uses_full_name_key_and_resolves_parent():
    adult_time = categories.CATEGORY_TABLE[33]
    child = adult_time["subs"][1]
    assert child["key"] == "adulttime-21sextury-nudefightclub"
    assert child["name"] == "NudeFightClub"
    assert child["parent"] == "adulttime-21sextury"


def test_nested_sub_without_matching_parent_has_none():
    old = categories.CATEGORY_TABLE[0]
    child = old["subs"][1]
    assert child["name"] == "SB"
    assert child["parent"] is None


def test_resolve_major_sub_and_unknown():
    assert categories.resolve("kink") == (10140, 16680)
    assert categories.resolve("kink-hogtied") == (13445, 14430)
    assert categories.resolve("missing") is None


async def test_counts_use_strict_bisect_ranges(monkeypatch):
    reset_counts(monkeypatch, FakeCollection([90, 4, 89, 3, 34, 46, 47]))
    result = await categories.get_categories()
    old = result["categories"][0]
    assert result["counts_exact"] is True
    assert old["count"] == 5
    assert old["subs"][0]["count"] == 2


async def test_mongo_failure_uses_span_estimates(monkeypatch):
    reset_counts(monkeypatch, FakeCollection(error=RuntimeError("offline")))
    result = await categories.get_categories()
    old = result["categories"][0]
    assert result["counts_exact"] is False
    assert old["count"] == 86
    assert old["subs"][0]["count"] == 13


async def test_fresh_cache_skips_mongo(monkeypatch):
    reset_counts(monkeypatch, FakeCollection([4, 34]))
    await categories.get_categories()
    monkeypatch.setattr(
        categories,
        "get_collection",
        lambda: FakeCollection(error=RuntimeError("must not be called")),
    )
    result = await categories.get_categories()
    assert result["counts_exact"] is True
    assert result["categories"][0]["count"] == 2


async def test_expired_cache_reloads_counts(monkeypatch):
    reset_counts(monkeypatch, FakeCollection([4, 34]))
    await categories.get_categories()
    monkeypatch.setattr(
        categories, "get_collection", lambda: FakeCollection([4, 34, 50])
    )
    categories._count_cache_expires = 0.0
    result = await categories.get_categories()
    assert result["counts_exact"] is True
    assert result["categories"][0]["count"] == 3


async def test_failed_load_retries_after_expiry(monkeypatch):
    reset_counts(monkeypatch, FakeCollection(error=RuntimeError("offline")))
    result = await categories.get_categories()
    assert result["counts_exact"] is False
    monkeypatch.setattr(
        categories, "get_collection", lambda: FakeCollection([4, 34])
    )
    categories._count_cache_expires = 0.0
    result = await categories.get_categories()
    assert result["counts_exact"] is True
    assert result["categories"][0]["count"] == 2


async def test_refresh_failure_keeps_last_exact_counts(monkeypatch):
    reset_counts(monkeypatch, FakeCollection([4, 34]))
    await categories.get_categories()
    monkeypatch.setattr(
        categories,
        "get_collection",
        lambda: FakeCollection(error=RuntimeError("offline")),
    )
    categories._count_cache_expires = 0.0
    result = await categories.get_categories()
    assert result["counts_exact"] is True
    assert result["categories"][0]["count"] == 2


class SlowCursor(FakeCursor):
    async def to_list(self, length):
        await asyncio.sleep(0)
        return self.documents


class CountingCollection(FakeCollection):
    def __init__(self, ids):
        super().__init__(ids)
        self.loads = 0

    def find(self, query, projection):
        cursor = super().find(query, projection)
        if query["paramType"] == "vidParams":
            self.loads += 1
        return SlowCursor(cursor.documents)


async def test_concurrent_expired_refreshes_share_one_load(monkeypatch):
    collection = CountingCollection([4, 34])
    reset_counts(monkeypatch, collection)
    monkeypatch.setattr(categories, "_refresh_lock", asyncio.Lock())
    first, second = await asyncio.gather(
        categories.get_categories(), categories.get_categories()
    )
    assert collection.loads == 1
    assert first == second
    assert first["categories"][0]["count"] == 2


def test_categories_requires_auth(client):
    assert client.get("/api/categories").status_code == 401


async def test_categories_returns_table_for_stuff_channel(
    authed_client, monkeypatch
):
    reset_counts(monkeypatch, FakeCollection([4, 34]))
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    response = authed_client.get("/api/categories")
    body = response.json()
    assert response.status_code == 200
    assert body["channel"] == categories.STUFF_CHANNEL
    assert body["counts_exact"] is True
    assert body["categories"][0]["count"] == 2


def test_categories_returns_empty_table_for_other_channel(
    authed_client, monkeypatch
):
    monkeypatch.setattr(channels, "active_key", lambda: "other")
    response = authed_client.get("/api/categories")
    assert response.json() == {
        "channel": "other",
        "counts_exact": True,
        "categories": [],
    }


def test_videos_filters_category_and_uses_category_count(
    authed_client, monkeypatch
):
    seen = {}

    async def fake_list(**kwargs):
        seen.update(kwargs)
        return [{"id": 34}], 999999

    async def fake_categories():
        return {
            "counts_exact": True,
            "categories": [{"key": "old", "count": 7, "subs": []}],
        }

    reset_counts(monkeypatch, FakeCollection([34]))
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    monkeypatch.setattr(categories, "get_categories", fake_categories)
    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list)
    response = authed_client.get("/api/videos?category=old")
    assert response.json() == {
        "videos": [{"id": 34, "caption": None, "posted_ts": None}],
        "total": 7,
    }
    assert seen["cat_start"] == 3
    assert seen["cat_end"] == 90


def test_videos_resolves_open_marker_category_on_cold_start(
    authed_client, monkeypatch
):
    seen = {}

    async def fake_list(**kwargs):
        seen.update(kwargs)
        return [], 0

    reset_counts(monkeypatch, FakeCollection(
        ids=[38657, 38658],
        markers=[{
            "forwardFromMessageId": 38656,
            "text": "+++ #CrazyAsianGF #CAG #begin",
        }],
    ))
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list)
    response = authed_client.get(
        "/api/videos?category=rk2-crazyasiangf&before_id=40000"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert seen["before_id"] == 40000
    assert seen["cat_start"] == 38656
    assert seen["cat_end"] is None


def test_videos_rejects_unknown_category(authed_client, monkeypatch):
    reset_counts(monkeypatch, FakeCollection())
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    response = authed_client.get("/api/videos?category=missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown category"}


def test_videos_rejects_category_for_other_channel(authed_client, monkeypatch):
    monkeypatch.setattr(channels, "active_key", lambda: "other")
    response = authed_client.get("/api/videos?category=old")
    assert response.status_code == 400
    assert response.json() == {
        "detail": "categories unavailable for this channel"
    }


async def test_telegram_uses_exclusive_category_bounds(monkeypatch):
    seen = {}

    async def fake_get_messages(channel, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    await telegram.list_videos_with_total(
        limit=20, before_id=85, offset=3, cat_start=3, cat_end=90
    )
    assert seen["offset_id"] == 85
    assert seen["min_id"] == 3
    assert seen["add_offset"] == 3


async def test_telegram_caps_cursor_at_category_end(monkeypatch):
    seen = {}

    async def fake_get_messages(channel, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    await telegram.list_videos_with_total(
        before_id=100, cat_start=3, cat_end=90
    )
    assert seen["offset_id"] == 90


async def test_telegram_open_category_has_no_cursor_cap(monkeypatch):
    seen = {}

    async def fake_get_messages(channel, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    await telegram.list_videos_with_total(
        before_id=40000, cat_start=38656, cat_end=None
    )
    assert seen["offset_id"] == 40000
    assert seen["min_id"] == 38656
