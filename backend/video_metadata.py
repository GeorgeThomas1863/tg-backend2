"""Video metadata enrichment: captions and post dates from postData1.

Merges free-text captions and original post timestamps (sourced from the
postData1 forward log) onto /api/videos pages. Enrichment must never break or
slow-fail the video list, so every Mongo failure is logged and degrades to
null caption/posted_ts for the affected videos.

Also owns caption text search (search_videos): postData1 is the only thing
that can text-search (Telegram's message cursor can't), so search mode ranks
matches in Mongo, resolves the winners to live Telegram messages, and builds
the same item shape as the normal listing.
"""

import logging

import categories
import channels
import db
import telegram

logger = logging.getLogger(__name__)


async def fetch_captions_for_ids(ids: list[int]) -> dict[int, dict]:
    """Return {forwardFromMessageId: {caption, datePosted}} for the given ids."""
    if not ids:
        return {}
    try:
        cursor = db.postdata_collection().find(
            {"paramType": "vidParams", "forwardFromMessageId": {"$in": ids}},
            {"forwardFromMessageId": 1, "caption": 1, "datePosted": 1, "_id": 0},
        )
        documents = await cursor.to_list(None)
    except Exception:
        logger.exception("Failed to fetch captions for %d video id(s)", len(ids))
        return {}

    by_id = {}
    for document in documents:
        message_id = document.get("forwardFromMessageId")
        if isinstance(message_id, int):
            by_id[message_id] = document
    return by_id


def merge_captions(videos: list[dict], captions_by_id: dict[int, dict]) -> list[dict]:
    """Return copies of `videos` with caption/posted_ts merged in (null if missing)."""
    enriched = []
    for video in videos:
        entry = captions_by_id.get(video["id"])
        enriched.append({
            **video,
            "caption": _clean_caption(entry),
            "posted_ts": _clean_posted_ts(entry),
        })
    return enriched


def _clean_caption(entry: dict | None) -> str | None:
    caption = entry.get("caption") if entry else None
    return caption if isinstance(caption, str) else None


def _clean_posted_ts(entry: dict | None) -> int | None:
    posted_ts = entry.get("datePosted") if entry else None
    return posted_ts if isinstance(posted_ts, int) else None


async def enrich_videos(videos: list[dict]) -> list[dict]:
    """Attach caption/posted_ts metadata to a page of video dicts."""
    if not videos:
        return videos
    ids = [video["id"] for video in videos]
    captions_by_id = await fetch_captions_for_ids(ids)
    return merge_captions(videos, captions_by_id)


# --- caption text search ---


async def find_matching_ids(search: str, limit: int, offset: int) -> tuple[list[int], int, int] | None:
    """Text-search postData1 captions; return (ids in relevance order, total,
    matched_count).

    matched_count is the raw number of Mongo match documents consumed for
    this page, counted BEFORE dropping documents with no valid
    forwardFromMessageId — it's what search_videos builds next_offset from,
    not len(ids).

    limit=0 means "count only": a bare pymongo cursor treats .limit(0) as "no
    limit" rather than zero, so that case skips find/sort/skip entirely, just
    reports the true match count, and consumes no documents (matched_count=0).

    Returns None on any Mongo failure rather than degrading to an empty page
    — search must fail loud (502), like the non-search listing does.
    """
    query_filter = _search_filter(search)
    if limit == 0:
        total = await _count_matches(query_filter, search)
        if total is None:
            return None
        return [], total, 0

    try:
        cursor = (
            db.postdata_collection()
            .find(query_filter, {"forwardFromMessageId": 1, "score": {"$meta": "textScore"}, "_id": 0})
            .sort([("score", {"$meta": "textScore"})])
            .skip(offset)
            .limit(limit)
        )
        documents = await cursor.to_list(None)
        total = await db.postdata_collection().count_documents(query_filter)
    except Exception:
        logger.exception("Failed to search captions for %r", search)
        return None

    ids = []
    for document in documents:
        message_id = document.get("forwardFromMessageId")
        if isinstance(message_id, int):
            ids.append(message_id)
    return ids, total, len(documents)


def _search_filter(search: str) -> dict:
    return {"$text": {"$search": search}, "paramType": "vidParams"}


async def _count_matches(query_filter: dict, search: str) -> int | None:
    """Count postData1 caption matches without fetching any documents."""
    try:
        return await db.postdata_collection().count_documents(query_filter)
    except Exception:
        logger.exception("Failed to count caption matches for %r", search)
        return None


async def search_videos(search: str, limit: int, offset: int) -> tuple[list[dict], int, int] | None:
    """Search captions and resolve matches to a page shaped like the normal listing.

    Returns (video_items, total, next_offset) on success, or None if the
    Mongo search or the Telegram resolution failed — the route turns that
    into a 502, matching the non-search listing's fail-loud contract.

    next_offset is offset + the raw count of Mongo match documents consumed
    for this page (see find_matching_ids), so the client can keep paging
    correctly even when Telegram resolution drops deleted/no-media ids.

    before_id and category are meaningless here — Mongo relevance order
    replaces Telegram's message cursor entirely.
    """
    # postData1 only carries caption/forward data for the Stuff channel (see
    # categories.STUFF_CHANNEL); on any other active channel a caption search
    # has nothing to match against, so it's an empty page, not a Mongo query.
    if channels.active_key() != categories.STUFF_CHANNEL:
        return [], 0, offset

    result = await find_matching_ids(search, limit, offset)
    if result is None:
        return None
    ids, total, matched_count = result
    next_offset = offset + matched_count
    if not ids:
        return [], total, next_offset

    try:
        messages = await telegram.get_messages_by_ids_or_raise(ids)
    except Exception:
        logger.exception("Failed to resolve %d search result id(s)", len(ids))
        return None

    video_items = [telegram.media_to_dict(m) for m in messages]
    return await enrich_videos(video_items), total, next_offset
