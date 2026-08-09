"""Route coverage for runtime cache settings and effective status values."""

import threading

import prefetch
import settings


def test_cache_settings_requires_authentication(client):
    response = client.post("/api/cache/settings", json={})

    assert response.status_code == 401


def test_empty_cache_settings_change_does_nothing(authed_client, monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "change_cache_dir", lambda value: calls.append(value))
    monkeypatch.setattr(settings, "apply_max_gb", lambda value: calls.append(value))

    response = authed_client.post("/api/cache/settings", json={})

    assert response.json() == {"success": False, "message": "Nothing to change"}
    assert calls == []


def test_size_only_change_does_not_restart_prefetch(authed_client, monkeypatch):
    calls = []

    async def fake_apply_max_gb(value):
        calls.append(("size", value))
        return {"success": True, "message": "Cache size updated"}

    async def record_prefetch_call():
        calls.append(("prefetch", None))

    monkeypatch.setattr(settings, "apply_max_gb", fake_apply_max_gb)
    monkeypatch.setattr(prefetch, "stop", record_prefetch_call)
    monkeypatch.setattr(prefetch, "start", record_prefetch_call)

    response = authed_client.post(
        "/api/cache/settings", json={"cache_max_gb": 12.5}
    )

    assert response.json() == {"success": True, "message": "Cache size updated"}
    assert calls == [("size", 12.5)]


def test_dir_change_restarts_prefetch_and_deletes_old_cache(authed_client, monkeypatch):
    calls = []
    deletion_finished = threading.Event()

    async def fake_change_cache_dir(value):
        calls.append(("change", value))
        return {
            "success": True,
            "message": "Cache location updated",
            "changed": True,
            "old_root": "/old/cache",
        }

    async def fake_stop():
        calls.append(("stop", None))

    async def fake_start():
        calls.append(("start", None))

    def fake_delete_cache_tree(root):
        calls.append(("delete", root))
        deletion_finished.set()

    monkeypatch.setattr(settings, "change_cache_dir", fake_change_cache_dir)
    monkeypatch.setattr(settings, "delete_cache_tree", fake_delete_cache_tree)
    monkeypatch.setattr(prefetch, "stop", fake_stop)
    monkeypatch.setattr(prefetch, "start", fake_start)

    response = authed_client.post(
        "/api/cache/settings", json={"cache_dir": "/new/cache"}
    )

    assert response.json() == {"success": True, "message": "Cache location updated"}
    assert deletion_finished.wait(timeout=1)
    assert calls == [
        ("stop", None),
        ("change", "/new/cache"),
        ("start", None),
        ("delete", "/old/cache"),
    ]


def test_failed_dir_change_restarts_prefetch_without_deletion(
    authed_client, monkeypatch
):
    calls = []

    async def fake_change_cache_dir(value):
        calls.append("change")
        return {"success": False, "message": "Invalid cache location"}

    async def fake_stop():
        calls.append("stop")

    async def fake_start():
        calls.append("start")

    monkeypatch.setattr(settings, "change_cache_dir", fake_change_cache_dir)
    monkeypatch.setattr(settings, "apply_max_gb", lambda value: calls.append("size"))
    monkeypatch.setattr(settings, "delete_cache_tree", lambda root: calls.append("delete"))
    monkeypatch.setattr(prefetch, "stop", fake_stop)
    monkeypatch.setattr(prefetch, "start", fake_start)

    response = authed_client.post(
        "/api/cache/settings",
        json={"cache_dir": "/bad/cache", "cache_max_gb": 12.5},
    )

    assert response.json() == {"success": False, "message": "Invalid cache location"}
    assert calls == ["stop", "change", "start"]


def test_unchanged_dir_does_not_schedule_deletion(authed_client, monkeypatch):
    calls = []

    async def fake_change_cache_dir(value):
        return {
            "success": True,
            "message": "Cache location unchanged",
            "changed": False,
        }

    async def fake_prefetch_lifecycle():
        return None

    monkeypatch.setattr(settings, "change_cache_dir", fake_change_cache_dir)
    monkeypatch.setattr(settings, "delete_cache_tree", lambda root: calls.append(root))
    monkeypatch.setattr(prefetch, "stop", fake_prefetch_lifecycle)
    monkeypatch.setattr(prefetch, "start", fake_prefetch_lifecycle)

    response = authed_client.post(
        "/api/cache/settings", json={"cache_dir": "/same/cache"}
    )

    assert response.json() == {"success": True, "message": "Cache location unchanged"}
    assert calls == []


def test_cache_status_includes_effective_settings(authed_client, monkeypatch):
    monkeypatch.setattr(
        settings,
        "effective",
        lambda: {"cache_dir": "/effective/cache", "cache_max_gb": 24.0},
    )

    response = authed_client.get("/api/cache/status")

    assert response.json()["cache_dir"] == "/effective/cache"
    assert response.json()["max_gb"] == 24.0
