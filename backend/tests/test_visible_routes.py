import channels
import main
import prefetch


def test_visible_requires_auth(client):
    resp = client.post("/api/prefetch/visible", json={"ids": [1]})
    assert resp.status_code == 401


def test_visible_forwards_active_channel_and_ids(authed_client, monkeypatch):
    calls = []

    def fake_set_visible(channel_key, ids):
        calls.append((channel_key, ids))

    monkeypatch.setattr(prefetch, "set_visible", fake_set_visible)

    resp = authed_client.post("/api/prefetch/visible", json={"ids": [5, 3]})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert calls == [("test", [5, 3])]


def test_visible_without_active_channel_fails(authed_client, monkeypatch):
    calls = []
    monkeypatch.setattr(channels, "_active_channel", None)
    monkeypatch.setattr(prefetch, "set_visible", lambda *args: calls.append(args))

    resp = authed_client.post("/api/prefetch/visible", json={"ids": [5]})

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert calls == []


def test_visible_rejects_oversized_list(authed_client, monkeypatch):
    calls = []
    monkeypatch.setattr(prefetch, "set_visible", lambda *args: calls.append(args))
    ids = list(range(main.MAX_VISIBLE_IDS + 1))

    resp = authed_client.post("/api/prefetch/visible", json={"ids": ids})

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert calls == []


def test_visible_rejects_non_integer_ids(authed_client):
    resp = authed_client.post("/api/prefetch/visible", json={"ids": ["abc"]})
    assert resp.status_code == 422
