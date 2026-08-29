"""Playability verdict enrichment for /api/videos.

Batch-fetches precomputed playability verdicts (written offline by
scripts/check_playability.py) out of the "playability" Mongo collection and
attaches them to a page of video dicts. Enrichment must never break or
slow-fail the video list, so any Mongo failure is logged and degrades to a
null playability verdict for every video on the page.
"""

import logging

import categories
import channels
import db

logger = logging.getLogger(__name__)


async def fetch_verdicts_for_ids(ids: list[int]) -> dict[int, str]:
    """Return {msg_id: verdict} for the given ids."""
    if not ids:
        return {}
    try:
        cursor = db.playability_collection().find(
            {"_id": {"$in": ids}},
            {"verdict": 1},
        )
        documents = await cursor.to_list(None)
    except Exception:
        logger.exception("Failed to fetch playability verdicts for %d video id(s)", len(ids))
        return {}

    by_id = {}
    for document in documents:
        message_id = document.get("_id")
        verdict = document.get("verdict")
        if isinstance(message_id, int) and isinstance(verdict, str):
            by_id[message_id] = verdict
    return by_id


async def enrich_playability(videos: list[dict]) -> None:
    """Set item["playability"] (verdict string or None) on every video dict in place."""
    if not videos:
        return
    # Verdicts are keyed by bare msg id and were audited against the Stuff
    # channel (see categories.STUFF_CHANNEL); another channel's ids would
    # collide with them, so any other active channel gets null verdicts.
    if channels.active_key() != categories.STUFF_CHANNEL:
        for video in videos:
            video["playability"] = None
        return
    ids = [video["id"] for video in videos]
    verdicts_by_id = await fetch_verdicts_for_ids(ids)
    for video in videos:
        video["playability"] = verdicts_by_id.get(video["id"])
