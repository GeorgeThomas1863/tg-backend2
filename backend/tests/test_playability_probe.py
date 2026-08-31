import asyncio
import json
from pathlib import Path

import categories
import channels
import config
import db
import playability_probe


class FakeCollection:
    def __init__(self, existing=False):
        self.existing = existing
        self.updates = []
        self.finds = 0

    async def find_one(self, query, projection):
        self.finds += 1
        return {"_id": query["_id"]} if self.existing else None

    async def update_one(self, query, update, upsert):
        self.updates.append((query, update, upsert))


class FakeProcess:
    def __init__(self, stdout=b"{}", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def install_probe_world(monkeypatch, collection, blocks):
    monkeypatch.setattr(config, "PROBE_ENABLED", True)
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    monkeypatch.setattr(db, "playability_collection", lambda: collection)
    monkeypatch.setattr(playability_probe, "_ffprobe_path", "ffprobe")
    monkeypatch.setattr(playability_probe, "_ffprobe_checked", True)
    playability_probe._recorded_ids.clear()
    monkeypatch.setattr(playability_probe, "_probe_lock", asyncio.Lock())
    monkeypatch.setattr(
        playability_probe.cache,
        "has_block",
        lambda channel_key, msg_id, idx: idx in blocks,
    )
    monkeypatch.setattr(
        playability_probe.cache,
        "read_block",
        lambda channel_key, msg_id, idx: blocks.get(idx),
    )


def test_normalize_codec_and_infer_bit_depth():
    assert playability_probe.normalize_codec("HVC1") == "hevc"
    assert playability_probe.infer_bit_depth({"bits_per_raw_sample": "10"}, "yuv420p") == 10
    assert playability_probe.infer_bit_depth({}, "yuv420p10le") == 10


def test_derive_verdict_covers_windows_browser_rules():
    assert playability_probe.derive_verdict("h264", "yuv420p", 8, "aac") == "PLAYS"
    assert playability_probe.derive_verdict("h264", "yuv420p10le", 10, "aac") == "FAILS_10BIT"
    assert playability_probe.derive_verdict("hevc", "yuv420p", 8, "aac") == "RISK_HEVC"
    assert playability_probe.derive_verdict("h264", "yuv420p", 8, "ac3") == "AUDIO_FAILS"


def test_summarize_ffprobe_selects_first_video_and_audio_streams():
    raw = {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
            {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le"},
        ],
        "format": {"size": "12"},
    }

    summary = playability_probe.summarize_ffprobe(raw)

    assert summary["video"]["codec_name"] == "h264"
    assert summary["video"]["bit_depth"] == 8
    assert summary["audio"] == {"codec_name": "aac", "channels": 2}
    assert summary["format_size"] == "12"


def test_check_faststart_bytes_reports_moov_before_mdat():
    assert playability_probe.check_faststart_bytes(b"xxxxmoovxxxxmdat") is True
    assert playability_probe.check_faststart_bytes(b"xxxxmdatxxxxmoov") is False
    assert playability_probe.check_faststart_bytes(b"not-an-mp4") is None


async def test_probe_skips_existing_mongo_id(monkeypatch):
    collection = FakeCollection(existing=True)
    install_probe_world(monkeypatch, collection, {0: b"bytes"})

    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 7, 5)

    assert collection.updates == []


async def test_probe_skips_disabled_wrong_channel_and_missing_block(monkeypatch):
    collection = FakeCollection()
    install_probe_world(monkeypatch, collection, {0: b"first"})
    monkeypatch.setattr(config, "PROBE_ENABLED", False)
    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 1, 5)
    monkeypatch.setattr(config, "PROBE_ENABLED", True)
    monkeypatch.setattr(channels, "active_key", lambda: "other")
    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 2, 5)
    monkeypatch.setattr(channels, "active_key", lambda: categories.STUFF_CHANNEL)
    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 3, config.BLOCK_SIZE + 1)

    assert collection.updates == []


async def test_probe_assembles_blocks_in_order_and_stores_exact_shape(monkeypatch):
    collection = FakeCollection()
    blocks = {0: b"aaaamoov", 1: b"mdatbbbb"}
    install_probe_world(monkeypatch, collection, blocks)
    raw = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
        ],
        "format": {"size": "16"},
    }

    async def create_process(*args, **kwargs):
        assert Path(args[-1]).read_bytes() == b"aaaamoovmdatbbbb"
        return FakeProcess(json.dumps(raw).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(playability_probe.time, "time", lambda: 1234)

    await playability_probe.probe_and_store(
        categories.STUFF_CHANNEL, 9, config.BLOCK_SIZE + 1
    )

    assert collection.updates == [
        (
            {"_id": 9},
            {"$set": {
                "verdict": "PLAYS",
                "video_codec": "h264",
                "audio_codec": "aac",
                "faststart": True,
                "updated_ts": 1234,
            }},
            True,
        )
    ]


async def test_probe_failure_stores_nothing_and_allows_retry(monkeypatch):
    collection = FakeCollection()
    install_probe_world(monkeypatch, collection, {0: b"bytes"})
    launches = 0

    async def create_process(*args, **kwargs):
        nonlocal launches
        launches += 1
        return FakeProcess(stderr=b"broken", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 10, 5)
    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 10, 5)

    assert launches == 2
    assert collection.updates == []


async def test_probe_success_dedupes_later_attempts_without_mongo_query(monkeypatch):
    collection = FakeCollection()
    install_probe_world(monkeypatch, collection, {0: b"aaaamoov"})
    raw = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
        ],
    }
    launches = 0

    async def create_process(*args, **kwargs):
        nonlocal launches
        launches += 1
        return FakeProcess(json.dumps(raw).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 20, 5)
    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 20, 5)

    assert launches == 1
    assert len(collection.updates) == 1
    assert collection.finds == 1


async def test_probe_timeout_kills_process_and_stores_nothing(monkeypatch):
    collection = FakeCollection()
    install_probe_world(monkeypatch, collection, {0: b"bytes"})
    process = FakeProcess()

    async def create_process(*args, **kwargs):
        return process

    async def timeout(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(asyncio, "wait_for", timeout)

    await playability_probe.probe_and_store(categories.STUFF_CHANNEL, 11, 5)

    assert process.killed is True
    assert collection.updates == []


async def test_eviction_during_assembly_deletes_partial_temp_file(
    tmp_path, monkeypatch
):
    collection = FakeCollection()
    install_probe_world(monkeypatch, collection, {0: b"first", 1: b"second"})
    monkeypatch.setattr(
        playability_probe.cache,
        "read_block",
        lambda channel_key, msg_id, idx: b"first" if idx == 0 else None,
    )
    real_named_temp = playability_probe.tempfile.NamedTemporaryFile

    def local_named_temp(*args, **kwargs):
        return real_named_temp(*args, dir=tmp_path, **kwargs)

    monkeypatch.setattr(
        playability_probe.tempfile, "NamedTemporaryFile", local_named_temp
    )

    await playability_probe.probe_and_store(
        categories.STUFF_CHANNEL, 12, config.BLOCK_SIZE + 1
    )

    assert list(tmp_path.iterdir()) == []
    assert collection.updates == []
