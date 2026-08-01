"""
get_message TTL cache: repeated calls inside the TTL hit Telegram once;
expiry and invalidate_message force a re-fetch. client.get_messages is
monkeypatched; time is controlled via telegram.time.monotonic.
"""

from types import SimpleNamespace

import telegram


async def test_second_call_within_ttl_hits_telegram_once(monkeypatch):
    calls = install_fake_get_messages(monkeypatch)
    first = await telegram.get_message(1)
    second = await telegram.get_message(1)
    assert first is second
    assert calls["count"] == 1


async def test_expired_entry_is_refetched(monkeypatch):
    calls = install_fake_get_messages(monkeypatch)
    clock = install_fake_clock(monkeypatch, start=1000.0)
    await telegram.get_message(1)
    clock["now"] += 301
    await telegram.get_message(1)
    assert calls["count"] == 2


async def test_invalidate_forces_refetch(monkeypatch):
    calls = install_fake_get_messages(monkeypatch)
    await telegram.get_message(1)
    telegram.invalidate_message(1)
    await telegram.get_message(1)
    assert calls["count"] == 2


async def test_none_result_is_not_cached(monkeypatch):
    calls = install_fake_get_messages(monkeypatch, result=None)
    await telegram.get_message(1)
    await telegram.get_message(1)
    assert calls["count"] == 2


async def test_failure_returns_none(monkeypatch):
    async def exploding(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(telegram.client, "get_messages", exploding)
    telegram.invalidate_message(1)
    assert await telegram.get_message(1) is None


# --- helpers ---


def install_fake_get_messages(monkeypatch, result="default"):
    calls = {"count": 0}

    async def fake_get_messages(channel, ids):
        calls["count"] += 1
        if result == "default":
            return SimpleNamespace(id=ids, media=object())
        return result

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    telegram._msg_cache.clear()
    return calls


def install_fake_clock(monkeypatch, start):
    clock = {"now": start}
    monkeypatch.setattr(telegram.time, "monotonic", lambda: clock["now"])
    return clock
