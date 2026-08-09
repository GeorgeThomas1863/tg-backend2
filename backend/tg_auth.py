"""Serialized Telegram-account login transactions and lifecycle changes."""

import asyncio
import logging
import time
from pathlib import Path

from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeHashEmptyError,
    PhoneCodeInvalidError,
    PhoneHashExpiredError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
    PhonePasswordFloodError,
    SessionPasswordNeededError,
)

import cache
import downloader
import prefetch
import settings
import telegram
from config import AUTH_MAX_ATTEMPTS, AUTH_WINDOW_SECONDS
from rate_limit import AuthRateLimiter

logger = logging.getLogger(__name__)
LOGIN_TTL_SECONDS = 10 * 60

_pending: dict | None = None
_transition_lock = asyncio.Lock()
send_code_limiter = AuthRateLimiter(AUTH_MAX_ATTEMPTS, AUTH_WINDOW_SECONDS)
verification_limiter = AuthRateLimiter(AUTH_MAX_ATTEMPTS, AUTH_WINDOW_SECONDS)


class TelegramRequestError(Exception):
    """Raised when status cannot distinguish auth state due to transport failure."""


async def status() -> dict:
    """Return current Telegram identity and resumable login stage."""
    try:
        user = await telegram.client.get_me()
    except Exception as exc:
        logger.exception("Failed loading Telegram account status")
        raise TelegramRequestError from exc
    return {
        "authorized": user is not None,
        "user": _serialize_user(user),
        "pending_step": pending_step(),
    }


async def start_login(phone: str, client_ip: str) -> dict:
    """Send a login code and replace any prior pending transaction."""
    async with _transition_lock:
        retry_after = send_code_limiter.retry_after(client_ip)
        if retry_after is not None:
            return _limited(retry_after)
        send_code_limiter.record_failure(client_ip)
        return await _send_login_code(phone)


async def submit_code(code: str, client_ip: str) -> dict:
    """Verify the pending code and complete login or advance to 2FA."""
    async with _transition_lock:
        retry_after = verification_limiter.retry_after(client_ip)
        if retry_after is not None:
            return _limited(retry_after)
        pending = _current_pending("code")
        if pending is None:
            return _failure("No Telegram login is in progress")
        return await _verify_code(pending, code, client_ip)


async def submit_password(password: str, client_ip: str) -> dict:
    """Verify the pending two-step password and complete login."""
    async with _transition_lock:
        retry_after = verification_limiter.retry_after(client_ip)
        if retry_after is not None:
            return _limited(retry_after)
        if _current_pending("password") is None:
            return _failure("No Telegram password challenge is in progress")
        return await _verify_password(password, client_ip)


async def logout() -> dict:
    """Safely invalidate account-bound senders, caches, and client state."""
    async with _transition_lock:
        try:
            authorized = await _check_authorization()
        except Exception:
            logger.exception("Failed checking Telegram authorization before logout")
            return {
                "success": False,
                "message": "Could not verify Telegram login state. Try again.",
            }
        if not authorized:
            await _clear_unauthorized_account_state()
            return {
                "success": True,
                "message": "Telegram account already logged out",
                "authorized": False,
            }
        result = await _run_logout_steps()
        return {
            **result,
            "authorized": False,
        }


async def _check_authorization() -> bool:
    """Check authorization without hiding Telegram failures."""
    return await telegram.client.is_user_authorized()


async def _clear_unauthorized_account_state() -> None:
    """Remove local account data even when Telegram is already unauthorized."""
    await prefetch.stop()
    await downloader.disconnect_all()
    telegram.clear_messages()
    await _wipe_disk_cache()
    _clear_pending()


def pending_step() -> str | None:
    pending = _unexpired_pending()
    return pending["stage"] if pending is not None else None


async def _send_login_code(phone: str) -> dict:
    global _pending
    try:
        sent = await telegram.client.send_code_request(phone)
    except PhoneNumberInvalidError:
        return _failure("Invalid phone number")
    except PhoneNumberBannedError:
        return _failure("This phone number is banned")
    except PhoneNumberFloodError:
        return _limited(AUTH_WINDOW_SECONDS, "Too many code requests. Try again later.")
    except FloodWaitError as exc:
        return _limited(exc.seconds)
    except Exception:
        logger.exception("Failed sending Telegram login code")
        return _failure("Telegram request failed")
    _pending = {
        "phone": phone,
        "phone_code_hash": sent.phone_code_hash,
        "stage": "code",
        "expires_at": time.monotonic() + LOGIN_TTL_SECONDS,
    }
    return {"success": True, "message": "Login code sent", "next_step": "code"}


async def _verify_code(pending: dict, code: str, client_ip: str) -> dict:
    try:
        await telegram.client.sign_in(
            phone=pending["phone"],
            code=code,
            phone_code_hash=pending["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        pending["stage"] = "password"
        return {
            "success": True,
            "message": "Two-step verification password required",
            "authorized": False,
            "next_step": "password",
        }
    except PhoneCodeInvalidError:
        return _verification_failure(client_ip, "Invalid login code")
    except (PhoneCodeExpiredError, PhoneCodeHashEmptyError, PhoneHashExpiredError):
        _clear_pending()
        return _verification_failure(client_ip, "Login code expired")
    except FloodWaitError as exc:
        return _limited(exc.seconds)
    except Exception:
        logger.exception("Failed verifying Telegram login code")
        return _verification_failure(client_ip, "Telegram request failed")
    return await _complete_login(client_ip)


async def _verify_password(password: str, client_ip: str) -> dict:
    try:
        await telegram.client.sign_in(password=password)
    except PasswordHashInvalidError:
        return _verification_failure(client_ip, "Wrong two-step verification password")
    except PhonePasswordFloodError:
        return _limited(AUTH_WINDOW_SECONDS, "Too many attempts. Try again later.")
    except FloodWaitError as exc:
        return _limited(exc.seconds)
    except Exception:
        logger.exception("Failed verifying Telegram two-step password")
        return _verification_failure(client_ip, "Telegram request failed")
    return await _complete_login(client_ip)


async def _complete_login(client_ip: str) -> dict:
    user = await _load_authenticated_user()
    if user is None:
        return _verification_failure(client_ip, "Telegram request failed")
    await _prepare_account_cache(user.id)
    telegram.clear_messages()
    await downloader.disconnect_all()
    await prefetch.start()
    _clear_pending()
    verification_limiter.clear(client_ip)
    return {
        "success": True,
        "message": "Telegram account authenticated",
        "authorized": True,
        "next_step": None,
    }


async def _load_authenticated_user():
    try:
        return await telegram.client.get_me()
    except Exception:
        logger.exception("Failed loading newly authenticated Telegram account")
        return None


async def _prepare_account_cache(account_id: int) -> None:
    previous_id = await settings.get_last_account_id()
    if previous_id != account_id:
        await _wipe_disk_cache()
    await settings.set_last_account_id(account_id)


async def _run_logout_steps() -> dict:
    await prefetch.stop()
    await downloader.disconnect_all()
    telegram.clear_messages()
    session_path = _session_path()
    result = await _log_out_remote_account()
    await _remove_leftover_session(session_path)
    await _wipe_disk_cache()
    await telegram.rebuild_client()
    _clear_pending()
    return result


async def _log_out_remote_account() -> dict:
    try:
        await telegram.client.log_out()
    except Exception:
        logger.exception("Failed logging out the Telegram account")
        return _failure("Telegram logout failed; local session data was cleared.")
    return {"success": True, "message": "Telegram account logged out"}


async def _wipe_disk_cache() -> None:
    await asyncio.to_thread(settings.delete_cache_tree, cache.CACHE_ROOT)
    cache.reset_accounting()


def _session_path() -> Path:
    filename = getattr(getattr(telegram.client, "session", None), "filename", None)
    return Path(filename) if filename else Path("session.session")


async def _remove_leftover_session(path: Path) -> None:
    for attempt in range(3):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            logger.warning("Session file deletion attempt %s failed", attempt + 1)
            await asyncio.sleep(0.05)


def _current_pending(stage: str) -> dict | None:
    pending = _unexpired_pending()
    if pending is None or pending["stage"] != stage:
        return None
    return pending


def _unexpired_pending() -> dict | None:
    global _pending
    if _pending is not None and time.monotonic() >= _pending["expires_at"]:
        _pending = None
    return _pending


def _clear_pending() -> None:
    global _pending
    _pending = None


def _serialize_user(user) -> dict | None:
    if user is None:
        return None
    return {"id": user.id, "username": user.username, "phone": user.phone}


def _verification_failure(client_ip: str, message: str) -> dict:
    verification_limiter.record_failure(client_ip)
    return _failure(message)


def _failure(message: str) -> dict:
    return {"success": False, "message": message}


def _limited(seconds: int, message: str = "Too many attempts. Try again later.") -> dict:
    return {"success": False, "message": message, "retry_after": max(1, seconds)}
