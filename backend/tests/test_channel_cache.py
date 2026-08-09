"""Channel-scoped cache correctness checks."""

import cache


def test_cache_paths_include_channel_key(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)

    assert cache.build_block_path("example", 7, 2) == (
        tmp_path / "blocks" / "example" / "7" / "2.blk"
    )
    assert cache.build_thumb_path("example", 7) == (
        tmp_path / "thumbs" / "example" / "7.jpg"
    )


def test_stale_block_write_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache.channels, "active_key", lambda: "new")

    cache.write_block("old", 7, 2, b"stale")

    assert not cache.build_block_path("old", 7, 2).exists()


def test_stale_thumb_write_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache.channels, "active_key", lambda: "new")

    cache.write_thumb("old", 7, b"stale")

    assert not cache.build_thumb_path("old", 7).exists()
