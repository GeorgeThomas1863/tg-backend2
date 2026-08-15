"""
with_entity_warm: a ChannelInvalidError/ValueError from a channel request
triggers one get_dialogs sweep (refilling the entity cache a logout/login
wiped) and a single retry. The warm is cooldown-limited so a channel the
account truly cannot access does not loop dialog sweeps.
"""

from types import SimpleNamespace

from telethon.errors import ChannelInvalidError

import telegram


async def test_entity_error_warms_and_retries(monkeypatch):
    dialogs = install_fake_get_dialogs(monkeypatch)
    run = build_failing_then_ok_run(ChannelInvalidError(None), "ok")
    assert await telegram.with_entity_warm(run) == "ok"
    assert dialogs["count"] == 1


async def test_value_error_warms_and_retries(monkeypatch):
    dialogs = install_fake_get_dialogs(monkeypatch)
    run = build_failing_then_ok_run(ValueError("no entity"), "ok")
    assert await telegram.with_entity_warm(run) == "ok"
    assert dialogs["count"] == 1


async def test_unrelated_errors_do_not_warm(monkeypatch):
    dialogs = install_fake_get_dialogs(monkeypatch)
    run = build_failing_then_ok_run(RuntimeError("boom"), "ok")
    try:
        await telegram.with_entity_warm(run)
    except RuntimeError:
        pass
    else:
        raise AssertionError("RuntimeError should propagate")
    assert dialogs["count"] == 0


async def test_warm_on_cooldown_propagates_error(monkeypatch):
    install_fake_get_dialogs(monkeypatch)
    monkeypatch.setattr(telegram, "_last_entity_warm", telegram.time.monotonic())
    run = build_failing_then_ok_run(ChannelInvalidError(None), "ok")
    try:
        await telegram.with_entity_warm(run)
    except ChannelInvalidError:
        pass
    else:
        raise AssertionError("ChannelInvalidError should propagate on cooldown")


async def test_failed_warm_propagates_original_error(monkeypatch):
    async def exploding_get_dialogs():
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram.client, "get_dialogs", exploding_get_dialogs)
    monkeypatch.setattr(telegram, "_last_entity_warm", None)
    run = build_failing_then_ok_run(ChannelInvalidError(None), "ok")
    try:
        await telegram.with_entity_warm(run)
    except ChannelInvalidError:
        pass
    else:
        raise AssertionError("ChannelInvalidError should propagate when warm fails")


async def test_get_message_recovers_after_warm(monkeypatch):
    dialogs = install_fake_get_dialogs(monkeypatch)
    calls = {"count": 0}

    async def flaky_get_messages(channel, ids):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ChannelInvalidError(None)
        return SimpleNamespace(id=ids, media=object())

    monkeypatch.setattr(telegram.client, "get_messages", flaky_get_messages)
    telegram._msg_cache.clear()
    msg = await telegram.get_message(1)
    assert msg is not None and msg.id == 1
    assert dialogs["count"] == 1
    assert calls["count"] == 2


# --- helpers ---


def install_fake_get_dialogs(monkeypatch):
    calls = {"count": 0}

    async def fake_get_dialogs():
        calls["count"] += 1
        return []

    monkeypatch.setattr(telegram.client, "get_dialogs", fake_get_dialogs)
    monkeypatch.setattr(telegram, "_last_entity_warm", None)
    return calls


def build_failing_then_ok_run(error, result):
    state = {"failed": False}

    async def run():
        if not state["failed"]:
            state["failed"] = True
            raise error
        return result

    return run
