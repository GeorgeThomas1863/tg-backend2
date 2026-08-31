"""Disk-only playability probing for fully cached Stuff videos."""

import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import time

import cache
import categories
import channels
import config
import db

logger = logging.getLogger(__name__)

AUDIO_FAIL_CODECS = {"ac3", "eac3", "dts", "truehd"}
CODEC_ALIASES = {
    "h264": "h264", "avc": "h264", "avc1": "h264",
    "h265": "hevc", "hevc": "hevc", "hev1": "hevc", "hvc1": "hevc",
    "av1": "av1", "av01": "av1",
    "vp9": "vp9", "vp8": "vp8",
}
MAX_PROBE_BYTES = 4 * 1024**3
PROBE_TIMEOUT_SECONDS = 60
FASTSTART_PREFIX_BYTES = 64 * 1024

# Ids whose verdict is known to exist in Mongo; failed probes stay retryable.
_recorded_ids: set[int] = set()
_probe_lock = asyncio.Lock()
_ffprobe_checked = False
_ffprobe_path: str | None = None


def should_probe(channel_key: str, file_size: int) -> bool:
    """Return whether a completion event is eligible for a cache scan."""
    if not config.PROBE_ENABLED:
        return False
    if channels.active_key() != categories.STUFF_CHANNEL:
        return False
    if channel_key != categories.STUFF_CHANNEL:
        return False
    if file_size > MAX_PROBE_BYTES:
        logger.debug("Skipping playability probe over 4GB")
        return False
    return True


async def probe_and_store(channel_key: str, msg_id: int, file_size: int) -> None:
    """Probe one fully cached video and persist its browser verdict."""
    if not should_probe(channel_key, file_size):
        return
    if resolve_ffprobe() is None:
        return
    if msg_id in _recorded_ids:
        logger.debug("Skipping recorded playability verdict for msg_id=%s", msg_id)
        return

    try:
        collection = db.playability_collection()
        # The Mongo check lives inside the lock so two completion events for
        # the same video cannot both pass it and probe twice.
        async with _probe_lock:
            existing = await collection.find_one({"_id": msg_id}, {"_id": 1})
            if existing is not None:
                _recorded_ids.add(msg_id)
                return
            stored = await probe_cached_video(
                collection, channel_key, msg_id, file_size
            )
            if stored:
                _recorded_ids.add(msg_id)
    except Exception:
        logger.exception("Playability probe failed for msg_id=%s", msg_id)


def resolve_ffprobe() -> str | None:
    """Resolve ffprobe once and warn once when it is unavailable."""
    global _ffprobe_checked, _ffprobe_path
    if _ffprobe_checked:
        return _ffprobe_path
    _ffprobe_checked = True
    _ffprobe_path = shutil.which("ffprobe")
    if _ffprobe_path is None:
        logger.warning("ffprobe is unavailable; playability probing is disabled")
    return _ffprobe_path


async def probe_cached_video(
    collection, channel_key: str, msg_id: int, file_size: int
) -> bool:
    """Assemble, inspect, and store one cached video; report whether it stored."""
    # Block scans and assembly copy up to 4 GiB of disk data, so they run in
    # a thread to keep the event loop (and active streams) responsive.
    if not await asyncio.to_thread(has_all_blocks, channel_key, msg_id, file_size):
        return False
    temp_path = None
    try:
        temp_path = await asyncio.to_thread(
            assemble_temp_video, channel_key, msg_id, file_size
        )
        if temp_path is None:
            return False
        faststart = await asyncio.to_thread(read_faststart, temp_path)
        raw = await run_ffprobe(temp_path)
        summary = summarize_ffprobe(raw)
        document = build_playability_document(msg_id, summary, faststart)
        await store_document(collection, document)
        return True
    finally:
        delete_temp_file(temp_path)


def has_all_blocks(channel_key: str, msg_id: int, file_size: int) -> bool:
    block_count = calculate_block_count(file_size)
    for block_idx in range(block_count):
        if not cache.has_block(channel_key, msg_id, block_idx):
            return False
    return True


def calculate_block_count(file_size: int) -> int:
    return (file_size + config.BLOCK_SIZE - 1) // config.BLOCK_SIZE


def assemble_temp_video(
    channel_key: str, msg_id: int, file_size: int
) -> Path | None:
    """Concatenate cached blocks into a closed system-temp file."""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".video")
    temp_path = Path(temp_file.name)
    is_complete = True
    try:
        with temp_file:
            for block_idx in range(calculate_block_count(file_size)):
                data = cache.read_block(channel_key, msg_id, block_idx)
                if data is None:
                    is_complete = False
                    break
                temp_file.write(data)
    except Exception:
        delete_temp_file(temp_path)
        raise
    if not is_complete:
        delete_temp_file(temp_path)
        return None
    return temp_path


def read_faststart(path: Path) -> bool | None:
    with path.open("rb") as source:
        return check_faststart_bytes(source.read(FASTSTART_PREFIX_BYTES))


async def run_ffprobe(path: Path) -> dict:
    process = await asyncio.create_subprocess_exec(
        resolve_ffprobe(),
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=PROBE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("ffprobe timed out")
    except asyncio.CancelledError:
        # An orphaned ffprobe would hold the temp file open past cleanup.
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[:300]
        raise RuntimeError(f"ffprobe exit {process.returncode}: {detail}")
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError(f"ffprobe output was not JSON: {error}") from error


def build_playability_document(
    msg_id: int, summary: dict, faststart: bool | None
) -> dict:
    video = summary["video"]
    audio = summary["audio"]
    video_codec = video["codec_name"]
    audio_codec = audio["codec_name"]
    return {
        "_id": msg_id,
        "verdict": derive_verdict(
            video_codec, video["pix_fmt"], video["bit_depth"], audio_codec
        ),
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "faststart": faststart,
        "updated_ts": int(time.time()),
    }


async def store_document(collection, document: dict) -> None:
    fields = {key: value for key, value in document.items() if key != "_id"}
    await collection.update_one(
        {"_id": document["_id"]}, {"$set": fields}, upsert=True
    )


def delete_temp_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        logger.exception("Failed to delete playability probe temp file %s", path)


def normalize_codec(raw: str | None) -> str | None:
    if not raw:
        return None
    return CODEC_ALIASES.get(raw.lower(), raw.lower())


def infer_bit_depth(stream: dict, pix_fmt: str | None) -> int | None:
    raw_bits = stream.get("bits_per_raw_sample")
    if raw_bits not in (None, "N/A", "0"):
        try:
            return int(raw_bits)
        except (TypeError, ValueError):
            pass
    if pix_fmt and ("10le" in pix_fmt or "10be" in pix_fmt):
        return 10
    if pix_fmt:
        return 8
    return None


def derive_verdict(
    video_codec: str | None,
    pix_fmt: str | None,
    bit_depth: int | None,
    audio_codec: str | None,
) -> str:
    """Derive Chrome/Edge-on-Windows playability from ffprobe data."""
    is_10bit = bit_depth == 10 or (
        pix_fmt is not None and ("10le" in pix_fmt or "10be" in pix_fmt)
    )
    if is_10bit:
        return "FAILS_10BIT"
    video_norm = normalize_codec(video_codec)
    if video_norm is None:
        return "UNKNOWN_NO_VIDEO_STREAM"
    if video_norm == "hevc":
        return "RISK_HEVC"
    if video_norm == "av1":
        return "DEPENDS_AV1"
    if video_norm != "h264":
        return f"UNKNOWN_VIDEO_{video_norm}"
    if audio_codec is not None and audio_codec.lower() in AUDIO_FAIL_CODECS:
        return "AUDIO_FAILS"
    if audio_codec is None or audio_codec.lower() in ("aac", "mp3"):
        return "PLAYS"
    return f"UNKNOWN_AUDIO_{audio_codec}"


def summarize_ffprobe(raw: dict | None) -> dict:
    streams = raw.get("streams", []) if raw else []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    return {
        "video": extract_video_stream_info(video_stream),
        "audio": extract_audio_stream_info(audio_stream),
        "format_size": (raw.get("format") or {}).get("size") if raw else None,
    }


def extract_video_stream_info(stream: dict | None) -> dict:
    if not stream:
        return {
            "codec_name": None, "codec_tag_string": None, "profile": None,
            "pix_fmt": None, "bit_depth": None, "level": None,
        }
    pix_fmt = stream.get("pix_fmt")
    return {
        "codec_name": stream.get("codec_name"),
        "codec_tag_string": stream.get("codec_tag_string"),
        "profile": stream.get("profile"),
        "pix_fmt": pix_fmt,
        "bit_depth": infer_bit_depth(stream, pix_fmt),
        "level": stream.get("level"),
    }


def extract_audio_stream_info(stream: dict | None) -> dict:
    if not stream:
        return {"codec_name": None, "channels": None}
    return {"codec_name": stream.get("codec_name"), "channels": stream.get("channels")}


def check_faststart_bytes(prefix: bytes) -> bool | None:
    """Return whether moov precedes mdat in a container prefix."""
    moov_pos = prefix.find(b"moov")
    mdat_pos = prefix.find(b"mdat")
    if moov_pos == -1 and mdat_pos == -1:
        return None
    return moov_pos != -1 and (mdat_pos == -1 or moov_pos < mdat_pos)
