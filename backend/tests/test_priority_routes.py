from types import SimpleNamespace

import cache
import channels
import prefetch
import telegram


def make_msg(msg_id, file_size):
    return SimpleNamespace(id=msg_id, file=SimpleNamespace(size=file_size), media=object())


def install_get_message(monkeypatch, msg):
    async def fake_get_message(msg_id, channel_key=None):
        return msg

    monkeypatch.setattr(telegram, "get_message", fake_get_message)


def test_priority_requires_auth(client):
    resp = client.post("/api/prefetch/priority", json={"id": 1})
    assert resp.status_code == 401


def test_priority_forwards_active_channel_and_id(authed_client, monkeypatch):
    calls = []

    def fake_set_priority(channel_key, msg_id):
        calls.append((channel_key, msg_id))

    install_get_message(monkeypatch, make_msg(5, 1024))
    monkeypatch.setattr(cache, "video_totals", lambda: {})
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


def test_priority_rejects_a_file_larger_than_the_cache_cap(authed_client, monkeypatch):
    calls = []
    huge_size = 12 * 1024**3  # 12 GB
    monkeypatch.setattr(cache, "MAX_BYTES", 10 * 1024**3)  # 10 GB cap
    install_get_message(monkeypatch, make_msg(9, huge_size))
    monkeypatch.setattr(prefetch, "set_priority", lambda *args: calls.append(args))

    resp = authed_client.post("/api/prefetch/priority", json={"id": 9})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "12.0 GB" in body["message"]
    assert "10.0 GB" in body["message"]
    assert calls == []


def test_priority_for_an_already_cached_video_reports_success_without_queueing(
    authed_client, monkeypatch
):
    calls = []
    install_get_message(monkeypatch, make_msg(3, 1024))
    monkeypatch.setattr(cache, "video_totals", lambda: {3: 1024})
    monkeypatch.setattr(prefetch, "set_priority", lambda *args: calls.append(args))

    resp = authed_client.post("/api/prefetch/priority", json={"id": 3})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "Already fully cached"
    assert calls == []


def test_priority_for_an_unresolvable_message_fails(authed_client, monkeypatch):
    calls = []
    install_get_message(monkeypatch, None)
    monkeypatch.setattr(prefetch, "set_priority", lambda *args: calls.append(args))

    resp = authed_client.post("/api/prefetch/priority", json={"id": 404})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert calls == []
