"""HTTP contract coverage for Telegram account authentication."""

import main
import tg_auth


def test_telegram_status_requires_site_auth(client):
    assert client.get("/api/telegram/status").status_code == 401


def test_telegram_status_returns_contract_shape(authed_client, monkeypatch):
    async def status():
        return {"authorized": False, "user": None, "pending_step": "code"}

    monkeypatch.setattr(tg_auth, "status", status)
    response = authed_client.get("/api/telegram/status")
    assert response.json() == {"authorized": False, "user": None, "pending_step": "code"}


def test_telegram_status_transport_failure_is_502(authed_client, monkeypatch):
    async def status():
        raise tg_auth.TelegramRequestError

    monkeypatch.setattr(tg_auth, "status", status)
    response = authed_client.get("/api/telegram/status")
    assert response.status_code == 502
    assert response.json() == {"detail": "Telegram request failed"}


def test_login_start_rate_limit_has_retry_after(authed_client, monkeypatch):
    async def start_login(phone, client_ip):
        return {"success": False, "message": "wait", "retry_after": 7}

    monkeypatch.setattr(tg_auth, "start_login", start_login)
    response = authed_client.post("/api/telegram/login/start", json={"phone": "+1555"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.json() == {"success": False, "message": "wait"}
