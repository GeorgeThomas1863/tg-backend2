"""
Disk caches: video blocks and thumbnails.

Video bytes are cached as whole fixed-size blocks keyed by (msg_id,
block_idx); the final block of a file is naturally shorter. Reads touch the
file mtime; writes are atomic (temp file + os.replace) and trigger LRU
eviction once the total passes MAX_BYTES. Thumbs are tiny and uncapped.
Every failure degrades to a cache miss — the cache is an optimization,
never a correctness dependency.
"""

import os
import traceback
from pathlib import Path

from config import CACHE_DIR, CACHE_MAX_GB

CACHE_ROOT = Path(CACHE_DIR)
MAX_BYTES = int(CACHE_MAX_GB * 1024**3)

_total_bytes = None  # lazily initialised; rebuilt by scan after restart
_video_bytes: dict[int, int] | None = None


# --- blocks ---


def read_block(msg_id: int, block_idx: int) -> bytes | None:
    """Return a cached block (touching its mtime for LRU), or None."""
    path = build_block_path(msg_id, block_idx)
    try:
        data = path.read_bytes()
        os.utime(path)
        return data
    except OSError:
        return None


def write_block(msg_id: int, block_idx: int, data: bytes) -> None:
    """Atomically store a block, then evict oldest blocks over the cap."""
    if not data:
        return
    path = build_block_path(msg_id, block_idx)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        report_error(f"writing block {msg_id}/{block_idx}")
        return
    grow_accounting(msg_id, len(data))
    evict_until_under_cap()


def has_block(msg_id: int, block_idx: int) -> bool:
    return build_block_path(msg_id, block_idx).exists()


# --- thumbs ---


def read_thumb(msg_id: int) -> bytes | None:
    try:
        return build_thumb_path(msg_id).read_bytes()
    except OSError:
        return None


def write_thumb(msg_id: int, data: bytes) -> None:
    if not data:
        return
    path = build_thumb_path(msg_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        report_error(f"writing thumb {msg_id}")


# --- size accounting + eviction ---


def evict_until_under_cap() -> None:
    global _total_bytes, _video_bytes
    if current_total() <= MAX_BYTES:
        return
    for path, size in list_blocks_oldest_first():
        if _total_bytes <= MAX_BYTES:
            return
        try:
            path.unlink()
            _total_bytes -= size
            try:
                msg_id = int(path.parent.name)
            except ValueError:
                continue
            remaining = _video_bytes.get(msg_id, 0) - size
            if remaining <= 0:
                _video_bytes.pop(msg_id, None)
            else:
                _video_bytes[msg_id] = remaining
        except OSError:
            report_error(f"evicting {path}")


def current_total() -> int:
    initialise_accounting()
    return _total_bytes


def grow_total(added: int) -> None:
    global _total_bytes
    if _total_bytes is None:
        # First touch: the scan already sees the file just written —
        # adding `added` on top would double-count it.
        initialise_accounting()
        return
    _total_bytes += added


def grow_accounting(msg_id: int, added: int) -> None:
    global _total_bytes, _video_bytes
    if _total_bytes is None or _video_bytes is None:
        initialise_accounting()
        return
    _total_bytes += added
    _video_bytes[msg_id] = _video_bytes.get(msg_id, 0) + added


def video_totals() -> dict[int, int]:
    try:
        initialise_accounting()
        return _video_bytes.copy()
    except Exception:
        return {}


def initialise_accounting() -> None:
    global _total_bytes, _video_bytes
    if _total_bytes is not None and _video_bytes is not None:
        return
    _total_bytes, _video_bytes = scan_accounting()


def scan_total() -> int:
    total, _ = scan_accounting()
    return total


def scan_accounting() -> tuple[int, dict[int, int]]:
    total = 0
    videos = {}
    for path, size in iter_block_files():
        total += size
        try:
            msg_id = int(path.parent.name)
        except ValueError:
            continue
        videos[msg_id] = videos.get(msg_id, 0) + size
    return total, videos


def list_blocks_oldest_first() -> list:
    entries = []
    for path, size in iter_block_files():
        try:
            entries.append((path.stat().st_mtime, path, size))
        except OSError:
            continue
    entries.sort()
    return [(path, size) for _, path, size in entries]


def iter_block_files():
    root = CACHE_ROOT / "blocks"
    if not root.exists():
        return
    for path in root.rglob("*.blk"):
        try:
            yield path, path.stat().st_size
        except OSError:
            continue


# --- pure builders ---


def build_block_path(msg_id: int, block_idx: int) -> Path:
    return CACHE_ROOT / "blocks" / str(msg_id) / f"{block_idx}.blk"


def build_thumb_path(msg_id: int) -> Path:
    return CACHE_ROOT / "thumbs" / f"{msg_id}.jpg"


def report_error(context: str) -> None:
    print(f"CACHE ERROR {context}:\n{traceback.format_exc()}")
