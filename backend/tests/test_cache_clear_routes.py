"""Route coverage for clearing the active disk cache."""

import pytest

import cache
import prefetch
import settings


def test_clear_cache_requires_authentication(client, monkeypatch):
    calls = []

    async def fake_stop():
        calls.append("stop")

    async def fake_start():
        calls.append("start")

    monkeypatch.setattr(
        settings,
        "delete_cache_tree",
        lambda root: calls.append("delete"),
    )
    monkeypatch.setattr(cache, "reset_accounting", lambda: calls.append("reset"))
    monkeypatch.setattr(prefetch, "stop", fake_stop)
    monkeypatch.setattr(prefetch, "start", fake_start)

    response = client.post("/api/cache/clear")

    assert response.status_code == 401
    assert calls == []


def test_clear_cache_deletes_active_root_and_restarts_prefetch(
    authed_client, monkeypatch
):
    calls = []

    async def fake_stop():
        calls.append(("stop", None))

    async def fake_start():
        calls.append(("start", None))

    def fake_delete_cache_tree(root):
        calls.append(("delete", root))

    def fake_reset_accounting():
        calls.append(("reset", None))

    monkeypatch.setattr(settings, "delete_cache_tree", fake_delete_cache_tree)
    monkeypatch.setattr(cache, "reset_accounting", fake_reset_accounting)
    monkeypatch.setattr(prefetch, "stop", fake_stop)
    monkeypatch.setattr(prefetch, "start", fake_start)

    response = authed_client.post("/api/cache/clear")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Cache cleared"}
    assert calls == [
        ("stop", None),
        ("delete", cache.CACHE_ROOT),
        ("reset", None),
        ("start", None),
    ]


def test_clear_cache_restarts_prefetch_when_deletion_raises(
    authed_client, monkeypatch
):
    calls = []

    async def fake_stop():
        calls.append("stop")

    async def fake_start():
        calls.append("start")

    def fake_delete_cache_tree(root):
        calls.append("delete")
        raise RuntimeError("deletion failed")

    monkeypatch.setattr(settings, "delete_cache_tree", fake_delete_cache_tree)
    monkeypatch.setattr(cache, "reset_accounting", lambda: calls.append("reset"))
    monkeypatch.setattr(prefetch, "stop", fake_stop)
    monkeypatch.setattr(prefetch, "start", fake_start)

    with pytest.raises(RuntimeError, match="deletion failed"):
        authed_client.post("/api/cache/clear")

    assert calls == ["stop", "delete", "start"]
