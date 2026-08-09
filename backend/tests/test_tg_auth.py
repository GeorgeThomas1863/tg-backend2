"""Telegram account transaction and lifecycle coverage."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

import cache
import downloader
import prefetch
import settings
import telegram
import tg_auth
from rate_limit import AuthRateLimiter


@pytest.fixture(autouse=True)
def reset_auth_state(monkeypatch):
    monkeypatch.setattr(tg_auth, "_pending", None)
    monkeypatch.setattr(tg_auth, "send_code_limiter", AuthRateLimiter(10, 900))
    monkeypatch.setattr(tg_auth, "verification_limiter", AuthRateLimiter(10, 900))


@pytest.mark.asyncio
async def test_status_shapes_include_pending_step(monkeypatch):
    async def get_me():
        return None

    monkeypatch.setattr(telegram.client, "get_me", get_me)
    tg_auth._pending = {
        "phone": "+1555",
        "phone_code_hash": "hash",
        "stage": "code",
        "expires_at": float("inf"),
    }

    assert await tg_auth.status() == {
        "authorized": False,
        "user": None,
        "pending_step": "code",
    }

    async def get_user():
        return SimpleNamespace(id=123, username="name", phone="+1555")

    monkeypatch.setattr(telegram.client, "get_me", get_user)
    result = await tg_auth.status()
    assert result["authorized"] is True
    assert result["user"] == {"id": 123, "username": "name", "phone": "+1555"}


@pytest.mark.asyncio
async def test_start_login_success_and_limiter_counts_every_attempt(monkeypatch):
    async def send_code(phone):
        return SimpleNamespace(phone_code_hash="hash")

    monkeypatch.setattr(telegram.client, "send_code_request", send_code)
    monkeypatch.setattr(tg_auth, "send_code_limiter", AuthRateLimiter(1, 900))

    result = await tg_auth.start_login("+1555", "ip")
    limited = await tg_auth.start_login("+1555", "ip")

    assert result == {"success": True, "message": "Login code sent", "next_step": "code"}
    assert limited["retry_after"] > 0


@pytest.mark.asyncio
async def test_start_login_maps_invalid_phone(monkeypatch):
    async def send_code(phone):
        raise PhoneNumberInvalidError(request=None)

    monkeypatch.setattr(telegram.client, "send_code_request", send_code)
    assert await tg_auth.start_login("bad", "ip") == {
        "success": False,
        "message": "Invalid phone number",
    }


@pytest.mark.asyncio
async def test_code_can_advance_to_password(monkeypatch):
    tg_auth._pending = _pending("code")

    async def sign_in(**kwargs):
        raise SessionPasswordNeededError(request=None)

    monkeypatch.setattr(telegram.client, "sign_in", sign_in)
    result = await tg_auth.submit_code("12345", "ip")
    assert result["next_step"] == "password"
    assert tg_auth.pending_step() == "password"


@pytest.mark.asyncio
async def test_invalid_code_records_failure_and_eventually_limits(monkeypatch):
    tg_auth._pending = _pending("code")
    monkeypatch.setattr(tg_auth, "verification_limiter", AuthRateLimiter(1, 900))

    async def sign_in(**kwargs):
        raise PhoneCodeInvalidError(request=None)

    monkeypatch.setattr(telegram.client, "sign_in", sign_in)
    assert (await tg_auth.submit_code("bad", "ip"))["message"] == "Invalid login code"
    assert (await tg_auth.submit_code("bad", "ip"))["retry_after"] > 0


@pytest.mark.asyncio
async def test_code_without_pending_transaction_fails():
    assert await tg_auth.submit_code("12345", "ip") == {
        "success": False,
        "message": "No Telegram login is in progress",
    }


@pytest.mark.asyncio
async def test_wrong_password_and_missing_challenge(monkeypatch):
    assert (await tg_auth.submit_password("pw", "ip"))["success"] is False
    tg_auth._pending = _pending("password")

    async def sign_in(**kwargs):
        raise PasswordHashInvalidError(request=None)

    monkeypatch.setattr(telegram.client, "sign_in", sign_in)
    result = await tg_auth.submit_password("pw", "ip")
    assert result["message"] == "Wrong two-step verification password"


@pytest.mark.asyncio
async def test_login_completion_wipes_only_for_different_account(monkeypatch):
    calls = []

    async def get_last():
        return 1

    async def set_last(account_id):
        calls.append(("store", account_id))
        return True

    async def record_async(name):
        calls.append(name)

    monkeypatch.setattr(settings, "get_last_account_id", get_last)
    monkeypatch.setattr(settings, "set_last_account_id", set_last)
    monkeypatch.setattr(tg_auth, "_wipe_disk_cache", lambda: record_async("wipe"))
    monkeypatch.setattr(downloader, "disconnect_all", lambda: record_async("pools"))
    monkeypatch.setattr(prefetch, "start", lambda: record_async("start"))
    monkeypatch.setattr(telegram, "clear_messages", lambda: calls.append("messages"))
    tg_auth._pending = _pending("code")

    await tg_auth._prepare_account_cache(1)
    assert "wipe" not in calls
    await tg_auth._prepare_account_cache(2)
    assert "wipe" in calls


@pytest.mark.asyncio
async def test_logout_steps_run_in_order_and_remove_session(monkeypatch, tmp_path):
    steps = []
    session_path = tmp_path / "session.session"
    session_path.write_text("stale")

    async def record(name):
        steps.append(name)

    async def log_out():
        steps.append("logout")

    monkeypatch.setattr(prefetch, "stop", lambda: record("stop"))
    monkeypatch.setattr(downloader, "disconnect_all", lambda: record("pools"))
    monkeypatch.setattr(telegram, "clear_messages", lambda: steps.append("messages"))
    monkeypatch.setattr(telegram.client, "log_out", log_out)
    monkeypatch.setattr(tg_auth, "_session_path", lambda: session_path)
    monkeypatch.setattr(tg_auth, "_wipe_disk_cache", lambda: record("cache"))
    monkeypatch.setattr(telegram, "rebuild_client", lambda: record("rebuild"))

    result = await tg_auth._run_logout_steps()

    assert result == {"success": True, "message": "Telegram account logged out"}
    assert steps == ["stop", "pools", "messages", "logout", "cache", "rebuild"]
    assert not session_path.exists()


@pytest.mark.asyncio
async def test_already_unauthorized_logout_clears_all_local_account_state(monkeypatch):
    steps = []

    async def unauthorized():
        return False

    async def record(name):
        steps.append(name)

    monkeypatch.setattr(telegram.client, "is_user_authorized", unauthorized)
    monkeypatch.setattr(prefetch, "stop", lambda: record("stop"))
    monkeypatch.setattr(downloader, "disconnect_all", lambda: record("pools"))
    monkeypatch.setattr(telegram, "clear_messages", lambda: steps.append("messages"))
    monkeypatch.setattr(tg_auth, "_wipe_disk_cache", lambda: record("cache"))
    tg_auth._pending = _pending("code")

    result = await tg_auth.logout()

    assert result == {
        "success": True,
        "message": "Telegram account already logged out",
        "authorized": False,
    }
    assert steps == ["stop", "pools", "messages", "cache"]
    assert tg_auth.pending_step() is None


@pytest.mark.asyncio
async def test_logout_check_failure_preserves_local_account_state(monkeypatch):
    steps = []

    async def fail_check():
        raise RuntimeError("offline")

    monkeypatch.setattr(telegram.client, "is_user_authorized", fail_check)
    monkeypatch.setattr(tg_auth, "_clear_unauthorized_account_state", lambda: steps.append("clear"))
    monkeypatch.setattr(tg_auth, "_run_logout_steps", lambda: steps.append("logout"))
    tg_auth._pending = _pending("code")

    result = await tg_auth.logout()

    assert result == {
        "success": False,
        "message": "Could not verify Telegram login state. Try again.",
    }
    assert steps == []
    assert tg_auth.pending_step() == "code"


@pytest.mark.asyncio
async def test_remote_logout_failure_still_clears_local_state(monkeypatch, tmp_path):
    steps = []
    session_path = tmp_path / "session.session"
    session_path.write_text("stale")

    async def authorized():
        return True

    async def record(name):
        steps.append(name)

    async def fail_logout():
        steps.append("logout")
        raise RuntimeError("offline")

    monkeypatch.setattr(telegram.client, "is_user_authorized", authorized)
    monkeypatch.setattr(prefetch, "stop", lambda: record("stop"))
    monkeypatch.setattr(downloader, "disconnect_all", lambda: record("pools"))
    monkeypatch.setattr(telegram, "clear_messages", lambda: steps.append("messages"))
    monkeypatch.setattr(telegram.client, "log_out", fail_logout)
    monkeypatch.setattr(tg_auth, "_session_path", lambda: session_path)
    monkeypatch.setattr(tg_auth, "_wipe_disk_cache", lambda: record("cache"))
    monkeypatch.setattr(telegram, "rebuild_client", lambda: record("rebuild"))
    tg_auth._pending = _pending("code")

    result = await tg_auth.logout()

    assert result == {
        "success": False,
        "message": "Telegram logout failed; local session data was cleared.",
        "authorized": False,
    }
    assert steps == ["stop", "pools", "messages", "logout", "cache", "rebuild"]
    assert not session_path.exists()
    assert tg_auth.pending_step() is None


@pytest.mark.asyncio
async def test_overlapping_transitions_are_serialized(monkeypatch):
    events = []
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def send_code(phone):
        events.append(f"start:{phone}")
        if phone == "first":
            first_entered.set()
            await release_first.wait()
        events.append(f"end:{phone}")
        return SimpleNamespace(phone_code_hash=phone)

    monkeypatch.setattr(telegram.client, "send_code_request", send_code)
    first = asyncio.create_task(tg_auth.start_login("first", "one"))
    await first_entered.wait()
    second = asyncio.create_task(tg_auth.start_login("second", "two"))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first, second)

    assert events == ["start:first", "end:first", "start:second", "end:second"]


def _pending(stage: str) -> dict:
    return {
        "phone": "+1555",
        "phone_code_hash": "hash",
        "stage": stage,
        "expires_at": float("inf"),
    }
