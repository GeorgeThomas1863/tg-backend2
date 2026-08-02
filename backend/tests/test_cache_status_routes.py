import pytest

import cache
import prefetch


def test_cache_routes_require_authentication(client):
    assert client.get("/api/cache/status").status_code == 401
    assert client.post("/api/cache/paused", json={"paused": True}).status_code == 401


@pytest.mark.parametrize(
    "active",
    [
        None,
        {"msg_id": 42, "tier": "ahead"},
    ],
)
def test_cache_status_returns_exact_shape(authed_client, monkeypatch, active):
    status_calls = 0

    def fake_status():
        nonlocal status_calls
        status_calls += 1
        return {"paused": True, "active": active}

    monkeypatch.setattr(prefetch, "status", fake_status)
    monkeypatch.setattr(cache, "current_total", lambda: 1234)
    monkeypatch.setattr(cache, "MAX_BYTES", 5678)
    monkeypatch.setattr(cache, "video_totals", lambda: {11: 100, 22: 200})

    response = authed_client.get("/api/cache/status")

    assert response.status_code == 200
    assert response.json() == {
        "total_bytes": 1234,
        "max_bytes": 5678,
        "paused": True,
        "active": active,
        "videos": {"11": 100, "22": 200},
    }
    assert status_calls == 1


def test_cache_status_degrades_when_current_total_fails(authed_client, monkeypatch):
    def raise_accounting_error():
        raise OSError("cache accounting failed")

    monkeypatch.setattr(cache, "current_total", raise_accounting_error)
    monkeypatch.setattr(cache, "MAX_BYTES", 5678)
    monkeypatch.setattr(cache, "video_totals", lambda: {11: 100})
    monkeypatch.setattr(
        prefetch,
        "status",
        lambda: {"paused": True, "active": {"msg_id": 42, "tier": "ahead"}},
    )

    response = authed_client.get("/api/cache/status")

    assert response.status_code == 200
    assert response.json() == {
        "total_bytes": 0,
        "max_bytes": 5678,
        "paused": True,
        "active": {"msg_id": 42, "tier": "ahead"},
        "videos": {"11": 100},
    }


def test_set_cache_paused_updates_prefetch_state(authed_client):
    original_paused = prefetch.status()["paused"]
    try:
        paused_response = authed_client.post(
            "/api/cache/paused",
            json={"paused": True},
        )
        assert paused_response.status_code == 200
        assert paused_response.json() == {
            "success": True,
            "message": "Caching paused",
        }
        assert prefetch.status()["paused"] is True

        resumed_response = authed_client.post(
            "/api/cache/paused",
            json={"paused": False},
        )
        assert resumed_response.status_code == 200
        assert resumed_response.json() == {
            "success": True,
            "message": "Caching resumed",
        }
        assert prefetch.status()["paused"] is False
    finally:
        prefetch.set_paused(original_paused)


def test_set_cache_paused_rejects_invalid_body(authed_client):
    response = authed_client.post(
        "/api/cache/paused",
        json={"paused": "not-a-boolean"},
    )

    assert response.status_code == 422
