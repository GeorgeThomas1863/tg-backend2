"""Persistent runtime settings for the disk cache."""

import asyncio
import logging
import math
import shutil
import tempfile
from pathlib import Path

import cache
import config

logger = logging.getLogger(__name__)
_GIB = 1024**3

__all__ = [
    "startup",
    "effective",
    "apply_max_gb",
    "change_cache_dir",
    "cleanup_old_root",
    "delete_cache_tree",
]


def get_collection():
    """Return the patchable settings collection accessor."""
    from db import settings_collection

    return settings_collection()


async def startup() -> None:
    """Load, validate, and apply the persisted cache settings."""
    document = await _load_document()
    root = _document_root(document)
    max_gb = _document_max_gb(document)
    usable_root = _prepare_root(root)
    if usable_root is None:
        logger.error(
            "Cache directory %s is unusable; falling back to %s",
            root,
            config.CACHE_DIR,
        )
        usable_root = _prepare_root(Path(config.CACHE_DIR).expanduser().resolve())
    if usable_root is None:
        logger.error("Default cache directory %s is unusable", config.CACHE_DIR)
        usable_root = Path(config.CACHE_DIR).expanduser().resolve()
    cache.configure(usable_root, _gb_to_bytes(max_gb))


def effective() -> dict:
    """Return the cache settings currently active in this process."""
    return {
        "cache_dir": str(cache.CACHE_ROOT.resolve()),
        "cache_max_gb": cache.MAX_BYTES / _GIB,
    }


async def apply_max_gb(value) -> dict:
    """Persist and apply a positive cache size cap."""
    max_gb = _parse_max_gb(value)
    if max_gb is None:
        return _failure("Cache size must be a finite number greater than zero")
    if not await _persist({"cache_max_gb": max_gb}):
        return _failure("Could not save the cache size")
    cache.configure(cache.CACHE_ROOT, _gb_to_bytes(max_gb))
    await asyncio.to_thread(cache.evict_until_under_cap)
    return {"success": True, "message": "Cache size updated"}


async def change_cache_dir(raw) -> dict:
    """Validate, persist, and apply a new cache directory."""
    new_root, error = _parse_root(raw)
    if error:
        return _failure(error)
    current_root = cache.CACHE_ROOT.resolve()
    if new_root == current_root:
        return {
            "success": True,
            "message": "Cache location unchanged",
            "changed": False,
        }
    if _is_inside_cache_subtree(new_root, current_root):
        return _failure(
            "Cache location cannot be inside the current blocks or thumbs directory"
        )
    if _prepare_root(new_root) is None:
        return _failure("Cache location could not be created or written to")
    if not _claimable(new_root):
        return _failure(
            "Cache location already contains blocks or thumbs folders"
            " not created by this app"
        )
    if not await _persist({"cache_dir": str(new_root)}):
        return _failure("Could not save the cache location")
    cache.configure(new_root, cache.MAX_BYTES)
    return {
        "success": True,
        "message": "Cache location updated",
        "changed": True,
        "old_root": current_root,
    }


def cleanup_old_root(old_root) -> None:
    """Delete an abandoned cache root's subtrees unless it is active again."""
    if Path(old_root) == cache.CACHE_ROOT.resolve():
        logger.warning("Skipping old-cache cleanup; %s is active again", old_root)
        return
    delete_cache_tree(old_root)


def delete_cache_tree(root) -> None:
    """Delete blocks/thumbs beneath root only if this app claimed it."""
    root = Path(root)
    if not cache.is_owned(root):
        logger.warning("Refusing cache deletion in unowned directory %s", root)
        return
    for name in ("blocks", "thumbs"):
        try:
            shutil.rmtree(root / name, ignore_errors=True)
        except Exception:
            logger.exception("Failed deleting cache subtree %s", root / name)


async def _load_document() -> dict:
    try:
        return await get_collection().find_one({"_id": "cache"}) or {}
    except Exception:
        logger.exception("Failed loading cache settings; using environment defaults")
        return {}


async def _persist(values: dict) -> bool:
    try:
        await get_collection().update_one(
            {"_id": "cache"}, {"$set": values}, upsert=True
        )
        return True
    except Exception:
        logger.exception("Failed persisting cache settings")
        return False


def _document_root(document: dict) -> Path:
    raw = document.get("cache_dir")
    if raw is None:
        raw = config.CACHE_DIR
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        logger.exception("Invalid saved cache directory; using environment default")
        return Path(config.CACHE_DIR).expanduser().resolve()


def _document_max_gb(document: dict) -> float:
    parsed = _parse_max_gb(document.get("cache_max_gb"))
    return parsed if parsed is not None else float(config.CACHE_MAX_GB)


def _parse_max_gb(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _parse_root(raw) -> tuple[Path | None, str | None]:
    if not isinstance(raw, (str, Path)):
        return None, "Cache location must be a path"
    stripped = str(raw).strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        stripped = stripped[1:-1].strip()
    if not stripped:
        return None, "Cache location cannot be empty"
    path = Path(stripped).expanduser()
    if not path.is_absolute():
        return None, "Cache location must be an absolute path"
    try:
        return path.resolve(), None
    except (OSError, ValueError) as exc:
        return None, f"Cache location is invalid: {exc}"


def _prepare_root(root: Path) -> Path | None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".cache-probe-", delete=True):
            pass
        return root
    except (OSError, ValueError):
        logger.exception("Cache directory probe failed for %s", root)
        return None


def _claimable(root: Path) -> bool:
    if cache.is_owned(root):
        return True
    return not ((root / "blocks").exists() or (root / "thumbs").exists())


def _is_inside_cache_subtree(path: Path, current_root: Path) -> bool:
    return path.is_relative_to(current_root / "blocks") or path.is_relative_to(
        current_root / "thumbs"
    )


def _gb_to_bytes(value: float) -> int:
    return int(value * _GIB)


def _failure(message: str) -> dict:
    return {"success": False, "message": message}
