"""
Disk block cache: round-trips, atomicity, mtime-LRU eviction under the byte
cap, and failure-degrades-to-miss. Each test points CACHE_ROOT at its own
tmp_path and resets the lazy byte counter.
"""

import os

import cache


def test_read_miss_returns_none(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    assert cache.read_block(1, 0) is None


def test_write_then_read_round_trips(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    cache.write_block(1, 0, b"hello")
    assert cache.read_block(1, 0) == b"hello"
    assert cache.has_block(1, 0) is True


def test_write_leaves_no_tmp_file(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    cache.write_block(1, 0, b"data")
    leftovers = [p for p in (tmp_path / "blocks").rglob("*.tmp")]
    assert leftovers == []


def test_empty_data_is_not_written(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    cache.write_block(1, 0, b"")
    assert cache.has_block(1, 0) is False


def test_eviction_removes_oldest_first(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch, max_bytes=250)
    cache.write_block(1, 0, b"x" * 100)
    age_block(tmp_path, 1, 0, seconds=100)
    cache.write_block(1, 1, b"y" * 100)
    age_block(tmp_path, 1, 1, seconds=50)
    cache.write_block(2, 0, b"z" * 100)  # 300 > 250: evict oldest → (1,0)
    assert cache.has_block(1, 0) is False
    assert cache.has_block(1, 1) is True
    assert cache.has_block(2, 0) is True


def test_read_touches_mtime_for_lru(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch, max_bytes=250)
    cache.write_block(1, 0, b"x" * 100)
    age_block(tmp_path, 1, 0, seconds=100)
    cache.write_block(1, 1, b"y" * 100)
    age_block(tmp_path, 1, 1, seconds=50)
    cache.read_block(1, 0)               # touch: now (1,1) is oldest
    cache.write_block(2, 0, b"z" * 100)
    assert cache.has_block(1, 0) is True
    assert cache.has_block(1, 1) is False


def test_total_is_rebuilt_by_scanning_existing_files(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch, max_bytes=150)
    cache.write_block(1, 0, b"x" * 100)
    monkeypatch.setattr(cache, "_total_bytes", None)  # simulate restart
    cache.write_block(1, 1, b"y" * 100)               # scan finds 100 → evict
    assert cache.has_block(1, 0) is False


def test_thumb_round_trip_and_miss(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    assert cache.read_thumb(7) is None
    cache.write_thumb(7, b"\xff\xd8jpeg")
    assert cache.read_thumb(7) == b"\xff\xd8jpeg"


def test_write_failure_degrades_silently(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)

    def exploding_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", exploding_replace)
    cache.write_block(1, 0, b"data")     # must not raise
    assert cache.read_block(1, 0) is None


# --- helpers ---


def point_cache_at(tmp_path, monkeypatch, max_bytes=10**9):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache, "MAX_BYTES", max_bytes)
    monkeypatch.setattr(cache, "_total_bytes", None)


def age_block(tmp_path, msg_id, idx, seconds):
    path = tmp_path / "blocks" / str(msg_id) / f"{idx}.blk"
    old = path.stat().st_mtime - seconds
    os.utime(path, (old, old))
