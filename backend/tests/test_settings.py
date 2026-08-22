"""Runtime cache settings persistence and application."""

from pathlib import Path

import pytest

import cache
import config
import downloader
import settings


class FakeCollection:
    def __init__(self, document=None):
        self.document = document.copy() if document else None
        self.writes = 0

    async def find_one(self, query):
        if self.document and self.document.get("_id") == query.get("_id"):
            return self.document.copy()
        return None

    async def update_one(self, query, update, upsert=False):
        self.writes += 1
        if self.document is None:
            self.document = {"_id": query["_id"]}
        self.document.update(update["$set"])


class FailingCollection(FakeCollection):
    async def update_one(self, query, update, upsert=False):
        raise OSError("database unavailable")


def point_cache_at(root, max_bytes=10**9):
    cache.configure(root, max_bytes)


async def test_startup_uses_environment_defaults(tmp_path, monkeypatch):
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "CACHE_MAX_GB", 2.5)
    monkeypatch.setattr(config, "TG_CONNECTIONS", 4)

    await settings.startup()

    assert settings.effective() == {
        "cache_dir": str(tmp_path.resolve()),
        "cache_max_gb": 2.5,
        "tg_connections": 4,
    }
    assert cache.CACHE_ROOT == tmp_path.resolve()
    assert cache.MAX_BYTES == int(2.5 * 1024**3)


async def test_startup_applies_saved_overrides(tmp_path, monkeypatch):
    saved = tmp_path / "saved"
    collection = FakeCollection(
        {"_id": "cache", "cache_dir": str(saved), "cache_max_gb": 0.25}
    )
    monkeypatch.setattr(settings, "get_collection", lambda: collection)

    await settings.startup()

    assert cache.CACHE_ROOT == saved.resolve()
    assert cache.MAX_BYTES == int(0.25 * 1024**3)


async def test_startup_applies_saved_telegram_connections(tmp_path, monkeypatch):
    collection = FakeCollection({"_id": "cache", "tg_connections": 8})
    applied = []
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(downloader, "configure", applied.append)

    await settings.startup()

    assert applied == [8]


@pytest.mark.parametrize("value", [True, -1, 17, 2.5, "4"])
async def test_apply_tg_connections_rejects_invalid_values(value, monkeypatch):
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)

    result = await settings.apply_tg_connections(value)

    assert result == {
        "success": False,
        "message": "Telegram connections must be a whole number from 0 to 16",
    }
    assert collection.writes == 0


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (8, 8), (4.0, 4)])
async def test_apply_tg_connections_persists_and_applies(
    value, expected, monkeypatch
):
    collection = FakeCollection()
    applied = []
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    monkeypatch.setattr(downloader, "configure", applied.append)

    result = await settings.apply_tg_connections(value)

    assert result == {
        "success": True,
        "message": "Telegram connections updated",
    }
    assert collection.document["tg_connections"] == expected
    assert applied == [expected]


async def test_apply_tg_connections_does_not_apply_after_persist_failure(monkeypatch):
    applied = []
    monkeypatch.setattr(settings, "get_collection", FailingCollection)
    monkeypatch.setattr(downloader, "configure", applied.append)

    result = await settings.apply_tg_connections(8)

    assert result == {
        "success": False,
        "message": "Unable to save Telegram connections",
    }
    assert applied == []


async def test_startup_falls_back_when_saved_directory_is_file(tmp_path, monkeypatch):
    occupied = tmp_path / "occupied"
    occupied.write_text("file")
    fallback = tmp_path / "fallback"
    collection = FakeCollection({"_id": "cache", "cache_dir": str(occupied)})
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    monkeypatch.setattr(config, "CACHE_DIR", fallback)

    await settings.startup()

    assert cache.CACHE_ROOT == fallback.resolve()


async def test_apply_max_gb_rejects_invalid_values(tmp_path, monkeypatch):
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    point_cache_at(tmp_path)

    for value in (0, -1, "no", float("nan"), float("inf"), True):
        assert (await settings.apply_max_gb(value))["success"] is False

    assert collection.writes == 0


async def test_apply_max_gb_persists_and_evicts(tmp_path, monkeypatch):
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    point_cache_at(tmp_path)
    block = cache.build_block_path("test", 1, 0)
    block.parent.mkdir(parents=True)
    block.write_bytes(b"x" * 20)

    result = await settings.apply_max_gb(10 / 1024**3)

    assert result["success"] is True
    assert collection.document["cache_max_gb"] == 10 / 1024**3
    assert cache.MAX_BYTES == 10
    assert block.exists() is False


async def test_change_cache_dir_rejects_invalid_locations(tmp_path, monkeypatch):
    current = tmp_path / "current"
    current.mkdir()
    occupied = tmp_path / "occupied"
    occupied.write_text("file")
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    point_cache_at(current)

    for value in ("relative", current / "blocks" / "nested", occupied):
        assert (await settings.change_cache_dir(value))["success"] is False

    assert collection.writes == 0
    assert cache.CACHE_ROOT == current


async def test_change_cache_dir_applies_without_deleting_old_cache(tmp_path, monkeypatch):
    old_root = tmp_path / "old"
    old_block = old_root / "blocks" / "keep.blk"
    old_block.parent.mkdir(parents=True)
    old_block.write_bytes(b"old")
    new_root = tmp_path / "new"
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    point_cache_at(old_root)
    assert cache.current_total() == 3

    result = await settings.change_cache_dir(f'  "{new_root}"  ')

    assert result["changed"] is True
    assert result["old_root"] == old_root.resolve()
    assert cache.CACHE_ROOT == new_root.resolve()
    assert cache._total_bytes is None
    assert old_block.exists()
    assert collection.document["cache_dir"] == str(new_root.resolve())


async def test_change_cache_dir_current_path_is_noop(tmp_path, monkeypatch):
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    point_cache_at(tmp_path)

    result = await settings.change_cache_dir(str(tmp_path.resolve()))

    assert result == {
        "success": True,
        "message": "Cache location unchanged",
        "changed": False,
    }
    assert collection.writes == 0


def test_delete_cache_tree_preserves_root_and_siblings(tmp_path):
    cache.mark_owned(tmp_path)
    (tmp_path / "blocks").mkdir()
    (tmp_path / "thumbs").mkdir()
    sibling = tmp_path / "keep.txt"
    sibling.write_text("keep")

    settings.delete_cache_tree(tmp_path)

    assert tmp_path.is_dir()
    assert sibling.exists()
    assert not (tmp_path / "blocks").exists()
    assert not (tmp_path / "thumbs").exists()


def test_delete_cache_tree_refuses_unowned_root(tmp_path):
    foreign_block = tmp_path / "blocks" / "data.txt"
    foreign_block.parent.mkdir()
    foreign_block.write_text("not ours")
    (tmp_path / "thumbs").mkdir()

    settings.delete_cache_tree(tmp_path)

    assert foreign_block.exists()
    assert (tmp_path / "thumbs").exists()


async def test_change_cache_dir_rejects_foreign_cache_dirs(tmp_path, monkeypatch):
    current = tmp_path / "current"
    current.mkdir()
    foreign = tmp_path / "foreign"
    (foreign / "blocks").mkdir(parents=True)
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    point_cache_at(current)

    result = await settings.change_cache_dir(str(foreign))

    assert result["success"] is False
    assert collection.writes == 0
    assert cache.CACHE_ROOT == current


async def test_change_cache_dir_accepts_previously_owned_root(tmp_path, monkeypatch):
    current = tmp_path / "current"
    current.mkdir()
    reused = tmp_path / "reused"
    (reused / "blocks").mkdir(parents=True)
    cache.mark_owned(reused)
    collection = FakeCollection()
    monkeypatch.setattr(settings, "get_collection", lambda: collection)
    point_cache_at(current)

    result = await settings.change_cache_dir(str(reused))

    assert result["success"] is True
    assert result["changed"] is True
    assert cache.CACHE_ROOT == reused.resolve()


def test_cleanup_old_root_skips_active_root(tmp_path):
    point_cache_at(tmp_path)
    block = tmp_path / "blocks" / "keep.blk"
    block.parent.mkdir()
    block.write_bytes(b"keep")

    settings.cleanup_old_root(tmp_path)

    assert block.exists()


def test_cleanup_old_root_deletes_abandoned_root(tmp_path):
    old_root = tmp_path / "old"
    old_root.mkdir()
    cache.mark_owned(old_root)
    block = old_root / "blocks" / "gone.blk"
    block.parent.mkdir()
    block.write_bytes(b"gone")
    new_root = tmp_path / "new"
    new_root.mkdir()
    point_cache_at(new_root)

    settings.cleanup_old_root(old_root)

    assert not (old_root / "blocks").exists()
    assert old_root.is_dir()


def test_configure_reassigns_globals_and_rescans(tmp_path):
    first = tmp_path / "first"
    first_block = first / "blocks" / "test" / "1" / "0.blk"
    first_block.parent.mkdir(parents=True)
    first_block.write_bytes(b"first")
    cache.configure(first, 100)
    assert cache.current_total() == 5

    second = tmp_path / "second"
    second_block = second / "blocks" / "test" / "2" / "0.blk"
    second_block.parent.mkdir(parents=True)
    second_block.write_bytes(b"second-data")
    cache.configure(second, 200)

    assert cache.CACHE_ROOT == Path(second)
    assert cache.MAX_BYTES == 200
    assert cache.current_total() == 11
