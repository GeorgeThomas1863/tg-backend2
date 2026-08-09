"""Persistent Telegram channel registry and runtime active selection."""

import logging
import os
import re
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

import config

logger = logging.getLogger(__name__)

_active_channel: dict | None = None
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_NUMERIC_RE = re.compile(r"^-100\d+$")


def get_collection():
    """Return the patchable registry collection accessor."""
    from db import channels_collection

    return channels_collection()


async def startup() -> None:
    """Seed an empty registry and activate its default channel."""
    global _active_channel
    await _ensure_unique_channel_index()
    await _seed_registry()
    _active_channel = await get_collection().find_one({"is_default": True})


def get_active() -> int | str | None:
    """Return the active channel in Telethon's expected form."""
    if _active_channel is None:
        return None
    return config.parse_channel(_active_channel["channel"])


def active_key() -> str | None:
    """Return the filesystem-safe key for the active channel."""
    if _active_channel is None:
        return None
    return _active_channel["channel"].lower()


async def list_channels() -> list[dict]:
    """Return serialized registry entries."""
    documents = await get_collection().find({}).sort("added_at", 1).to_list(None)
    return [_serialize_channel(document) for document in documents]


async def add_channel(raw: str) -> dict:
    """Validate and add a normalized Telegram channel."""
    normalized = _normalize_channel(raw)
    if normalized is None:
        return _failure("Enter a valid channel username or -100... ID")

    collection = get_collection()
    title = await _resolve_title(normalized)
    if title is None:
        return _failure("Channel could not be resolved or accessed")

    is_first = await collection.count_documents({}) == 0
    document = _build_channel(normalized, title, is_first)
    try:
        result = await collection.insert_one(document)
    except DuplicateKeyError:
        return _failure("That channel is already saved")
    document["_id"] = result.inserted_id
    if is_first:
        _set_active_document(document)
    return {"success": True, "message": "Channel added", "channel": _serialize_channel(document)}


async def set_default(channel_id: str) -> dict:
    """Make one saved channel the sole default."""
    object_id = _parse_id(channel_id)
    if object_id is None:
        return _failure("Channel not found")

    collection = get_collection()
    if await collection.find_one({"_id": object_id}) is None:
        return _failure("Channel not found")
    await collection.update_many(
        {}, [{"$set": {"is_default": {"$eq": ["$_id", object_id]}}}]
    )
    return {"success": True, "message": "Default channel updated"}


async def set_active(channel_id: str) -> dict:
    """Select a saved channel for this process."""
    object_id = _parse_id(channel_id)
    if object_id is None:
        return _failure("Channel not found")
    document = await get_collection().find_one({"_id": object_id})
    if document is None:
        return _failure("Channel not found")
    _set_active_document(document)
    return {"success": True, "message": "Active channel updated"}


async def remove_channel(channel_id: str) -> dict:
    """Remove a channel unless it is active or default."""
    object_id = _parse_id(channel_id)
    if object_id is None:
        return _failure("Channel not found")
    collection = get_collection()
    document = await collection.find_one({"_id": object_id})
    if document is None:
        return _failure("Channel not found")
    if document.get("is_default"):
        return _failure("The default channel cannot be removed")
    if _active_channel and document["_id"] == _active_channel["_id"]:
        return _failure("The active channel cannot be removed")
    await collection.delete_one({"_id": object_id})
    return {"success": True, "message": "Channel removed"}


async def _seed_registry() -> None:
    collection = get_collection()
    if await collection.count_documents({}) > 0:
        return
    raw = os.environ.get("TELEGRAM_CHANNEL", "").strip()
    if not raw:
        return
    normalized = _normalize_channel(raw) or raw
    title = await _resolve_title(normalized) or normalized
    await collection.insert_one(_build_channel(normalized, title, True))


async def _ensure_unique_channel_index() -> None:
    try:
        await get_collection().create_index(
            "channel", unique=True, collation={"locale": "en", "strength": 2}
        )
    except Exception:
        logger.exception("Failed to create unique channel registry index")


async def _resolve_title(channel: str) -> str | None:
    try:
        import telegram
        from telethon.tl.types import Channel

        entity = await telegram.client.get_entity(config.parse_channel(channel))
    except Exception:
        logger.exception("Failed to resolve Telegram channel %r", channel)
        return None
    title = getattr(entity, "title", None)
    if not isinstance(entity, Channel) or not title:
        logger.warning("Resolved Telegram entity %r is not a channel", channel)
        return None
    return title


def _normalize_channel(raw: str) -> str | None:
    value = raw.strip() if isinstance(raw, str) else ""
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(?:www\.)?t\.me/", "", value, flags=re.IGNORECASE)
    value = value.rstrip("/")
    if value.startswith("@"):
        value = value[1:]
    if _NUMERIC_RE.fullmatch(value):
        return value
    if _USERNAME_RE.fullmatch(value):
        return value
    return None


def _build_channel(channel: str, title: str, is_default: bool) -> dict:
    return {
        "channel": channel,
        "title": title,
        "is_default": is_default,
        "added_at": datetime.now(timezone.utc),
    }


def _serialize_channel(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "channel": document["channel"],
        "title": document["title"],
        "is_default": document["is_default"],
        "is_active": bool(_active_channel and document["_id"] == _active_channel["_id"]),
    }


def _parse_id(channel_id: str) -> ObjectId | None:
    try:
        return ObjectId(channel_id)
    except (InvalidId, TypeError):
        return None


def _set_active_document(document: dict) -> None:
    global _active_channel
    _active_channel = document.copy()


def _failure(message: str) -> dict:
    return {"success": False, "message": message}
