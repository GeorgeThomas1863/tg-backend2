"""
Test bootstrap. The module-level order below is load-bearing:

1. Env vars are assigned directly (not setdefault) BEFORE config.py is
   imported — config reads them at import time, and its load_dotenv call
   never overrides already-set vars, so these values beat the repo-root .env
   and anything set in the shell.
2. The cwd is moved to a fresh temp dir BEFORE importing telegram —
   TelegramClient("session", ...) creates its SQLite session file relative
   to the cwd at import time, and it must never touch the real
   backend/session file (live Telegram auth).

Python's module cache means every later `import main` / `import telegram`
in test files reuses these already-initialised modules.
"""

import os
import tempfile

import bcrypt
import pytest

os.environ["TELEGRAM_API_ID"] = "12345"
os.environ["TELEGRAM_API_HASH"] = "test-hash"
os.environ["TELEGRAM_CHANNEL"] = "-1001234567890"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["PW_HASH"] = bcrypt.hashpw(
    b"test-password", bcrypt.gensalt(rounds=4)
).decode()
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="tg-backend-cache-")
os.environ["TG_CONNECTIONS"] = "2"

_original_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="tg-backend-tests-"))
import telegram  # noqa: E402  (must come after the chdir above)
import main  # noqa: E402
os.chdir(_original_cwd)

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    """TestClient whose lifespan is stubbed so Telethon never connects."""

    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    monkeypatch.setattr(telegram, "connect", fake_connect)
    monkeypatch.setattr(telegram, "disconnect", fake_disconnect)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def authed_client(client):
    """A client whose session cookie is already authenticated."""
    resp = client.post("/api/auth", json={"pw": "test-password"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    return client
