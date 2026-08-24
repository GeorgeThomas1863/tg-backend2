"""Sort direction for /api/videos: default ascending (oldest-first, item 2),
with sort=asc/after_id and sort=desc/before_id as the two paging modes.

Route-level tests capture the kwargs main.py forwards to
telegram.list_videos_with_total. Telegram-level tests capture the raw
get_messages() kwargs to prove the reverse=True query is built the way
Telethon's verified reverse semantics require (see telegram.py's
_build_video_query docstring for the citation).
"""

from telethon.tl.types import InputMessagesFilterVideo

import telegram


# --- route: default direction and cursor selection ---


def test_route_default_sort_is_ascending(authed_client, monkeypatch):
    seen = {}

    async def fake_list(**kwargs):
        seen.update(kwargs)
        return [], 0

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list)
    resp = authed_client.get("/api/videos")

    assert resp.status_code == 200
    assert seen["reverse"] is True
    assert seen["before_id"] is None
    assert seen["after_id"] is None


def test_route_sort_asc_forwards_after_id_for_forward_paging(authed_client, monkeypatch):
    seen = {}

    async def fake_list(**kwargs):
        seen.update(kwargs)
        return [], 0

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list)
    resp = authed_client.get("/api/videos?sort=asc&after_id=500&limit=25")

    assert resp.status_code == 200
    assert seen["after_id"] == 500
    assert seen["reverse"] is True
    assert seen["limit"] == 25


def test_route_sort_desc_reproduces_original_call(authed_client, monkeypatch):
    seen = {}

    async def fake_list(**kwargs):
        seen.update(kwargs)
        return [], 0

    monkeypatch.setattr(telegram, "list_videos_with_total", fake_list)
    resp = authed_client.get("/api/videos?sort=desc&before_id=1234&offset=8&limit=25")

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


def test_route_rejects_invalid_sort_value(authed_client):
    resp = authed_client.get("/api/videos?sort=bogus")
    assert resp.status_code == 422


# --- route: mismatched cursor + direction ---


def test_route_rejects_before_id_with_sort_asc(authed_client):
    resp = authed_client.get("/api/videos?sort=asc&before_id=10")
    assert resp.status_code == 400
    assert "after_id" in resp.json()["detail"]


def test_route_rejects_before_id_with_default_sort(authed_client):
    # sort defaults to asc, so a bare before_id (old desc-pagination style)
    # must be rejected rather than silently misinterpreted.
    resp = authed_client.get("/api/videos?before_id=10")
    assert resp.status_code == 400


def test_route_rejects_after_id_with_sort_desc(authed_client):
    resp = authed_client.get("/api/videos?sort=desc&after_id=10")
    assert resp.status_code == 400
    assert "before_id" in resp.json()["detail"]


# --- route: search mode ignores sort entirely ---


def test_route_search_mode_ignores_sort_and_mismatched_cursor(authed_client, monkeypatch):
    async def fake_search_videos(search, limit, offset):
        return [], 0, offset

    def fail(*args, **kwargs):
        raise AssertionError("search mode must not touch the Telegram message cursor")

    monkeypatch.setattr(telegram, "list_videos_with_total", fail)
    import video_metadata
    monkeypatch.setattr(video_metadata, "search_videos", fake_search_videos)

    # sort=asc + before_id would 400 outside search mode; search must ignore both.
    resp = authed_client.get("/api/videos?search=alice&sort=asc&before_id=999")

    assert resp.status_code == 200
    assert resp.json() == {"videos": [], "total": 0, "next_offset": 0}


# --- telegram.py: raw get_messages() kwargs ---


class MessageResults(list):
    def __init__(self, messages, total):
        super().__init__(messages)
        self.total = total


def _install_fake_get_messages(monkeypatch, seen):
    async def fake_get_messages(channel, **kwargs):
        seen.update(kwargs)
        return MessageResults([], 0)

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)


async def test_fetch_videos_desc_default_matches_original_kwargs_exactly(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(limit=10, before_id=500, offset=2)

    assert seen == {
        "limit": 10,
        "offset_id": 500,
        "add_offset": 2,
        "filter": InputMessagesFilterVideo,
    }


async def test_fetch_videos_reverse_true_passes_reverse_and_after_id_as_offset(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(limit=10, after_id=500, offset=2, reverse=True)

    assert seen["reverse"] is True
    assert seen["offset_id"] == 500
    # Negated: Telethon's reverse-mode chunking recomputes Telegram's raw
    # add_offset as (this value - limit) on every call, and Telegram's raw
    # add_offset always counts toward OLDER ids from offset_id regardless
    # of the reverse flag. Passing +2 here would walk the window toward
    # older (already-seen) messages instead of skipping 2 further ahead
    # into newer ones -- see _build_video_query's comment for the live
    # proof (sort=asc&offset=100 returned an empty page despite a correct
    # nonzero total, before this fix).
    assert seen["add_offset"] == -2
    assert "min_id" not in seen
    assert "max_id" not in seen


async def test_fetch_videos_reverse_true_no_after_id_starts_at_zero(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(limit=10, reverse=True)

    assert seen["offset_id"] == 0
    assert seen["reverse"] is True


async def test_fetch_videos_reverse_true_category_clamps_start_to_cat_start(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(
        limit=10, after_id=None, cat_start=100, cat_end=500, reverse=True
    )

    assert seen["offset_id"] == 100
    assert seen["max_id"] == 500
    assert "min_id" not in seen


async def test_fetch_videos_reverse_true_after_id_below_cat_start_clamps_up(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(
        limit=10, after_id=1, cat_start=100, cat_end=500, reverse=True
    )

    assert seen["offset_id"] == 100


async def test_fetch_videos_reverse_true_after_id_within_bounds_continues_paging(monkeypatch):
    # after_id deep inside the category must NOT be clamped back to
    # cat_start — otherwise every page of ascending category pagination
    # would restart from the oldest message.
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(
        limit=10, after_id=250, cat_start=100, cat_end=500, reverse=True
    )

    assert seen["offset_id"] == 250


async def test_fetch_videos_reverse_true_open_category_has_no_max_id(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(
        limit=10, after_id=40000, cat_start=38656, cat_end=None, reverse=True
    )

    assert seen["offset_id"] == 40000
    assert "max_id" not in seen


# --- telegram.py: the ascending-offset bug fix (add_offset must be negated
# in reverse mode) ---
#
# These assert the exact kwargs telegram.py builds, not real Telegram
# behavior -- Telethon's own internal reverse-mode chunking math (see
# _build_video_query's comment) is what actually turns a positive
# add_offset into an empty page; a fake get_messages() can't reproduce
# that. The live probe (GET /api/videos?sort=asc&offset=100 against the
# real backend, before vs. after this fix) is what proves the fix works.


async def test_fetch_videos_reverse_true_offset_negates_add_offset_no_category(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(limit=5, offset=100, reverse=True)

    assert seen == {
        "limit": 5,
        "offset_id": 0,
        "add_offset": -100,
        "reverse": True,
        "filter": InputMessagesFilterVideo,
    }


async def test_fetch_videos_reverse_true_offset_negates_add_offset_in_category(monkeypatch):
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(
        limit=5, offset=50, cat_start=100, cat_end=500, reverse=True
    )

    assert seen == {
        "limit": 5,
        "offset_id": 100,
        "add_offset": -50,
        "reverse": True,
        "max_id": 500,
        "filter": InputMessagesFilterVideo,
    }


async def test_fetch_videos_desc_offset_stays_positive_regression_guard(monkeypatch):
    """sort=desc must keep sending add_offset positive and unmodified --
    only reverse=True's Telethon chunking needs the negation, and this
    must not regress when the ascending fix lands."""
    seen = {}
    _install_fake_get_messages(monkeypatch, seen)

    await telegram.list_videos_with_total(limit=5, before_id=1000, offset=100)

    assert seen == {
        "limit": 5,
        "offset_id": 1000,
        "add_offset": 100,
        "filter": InputMessagesFilterVideo,
    }


async def test_fetch_videos_offset_zero_add_offset_unchanged_both_directions(monkeypatch):
    seen_asc = {}
    _install_fake_get_messages(monkeypatch, seen_asc)
    await telegram.list_videos_with_total(limit=5, reverse=True)
    assert seen_asc["add_offset"] == 0

    seen_desc = {}
    _install_fake_get_messages(monkeypatch, seen_desc)
    await telegram.list_videos_with_total(limit=5)
    assert seen_desc["add_offset"] == 0
