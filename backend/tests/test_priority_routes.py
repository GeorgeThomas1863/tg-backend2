import channels
import prefetch


def test_priority_requires_auth(client):
    resp = client.post("/api/prefetch/priority", json={"id": 1})
    assert resp.status_code == 401


def test_priority_forwards_active_channel_and_id(authed_client, monkeypatch):
    calls = []

    def fake_set_priority(channel_key, msg_id):
        calls.append((channel_key, msg_id))

    monkeypatch.setattr(prefetch, "set_priority", fake_set_priority)

    resp = authed_client.post("/api/prefetch/priority", json={"id": 5})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert calls == [("test", 5)]


def test_priority_without_active_channel_fails(authed_client, monkeypatch):
    calls = []
    monkeypatch.setattr(channels, "_active_channel", None)
    monkeypatch.setattr(prefetch, "set_priority", lambda *args: calls.append(args))

    resp = authed_client.post("/api/prefetch/priority", json={"id": 5})

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert calls == []


def test_priority_rejects_non_integer_id(authed_client):
    resp = authed_client.post("/api/prefetch/priority", json={"id": "abc"})
    assert resp.status_code == 422
