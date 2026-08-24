"""
Playability measurement harness.

Samples videos from the live channel, pulls ground truth from two independent
sources, and derives a browser-playability verdict:
  1. Telegram's self-reported DocumentAttributeVideo.video_codec (may be None
     — Telegram only sets it when its own server detected the codec).
  2. ffprobe pointed at this app's own /stream/{id} HTTP endpoint (issues Range
     requests, so it costs a few MB per video instead of a full download, and
     it exercises the exact serving path the browser uses).

This is a measurement tool only — it does not change how videos are served.
Results accumulate in a JSON file keyed by video id, so re-running is cheap
(existing ids are skipped unless --force) and the file grows into a queryable
record across many runs.

Requires the backend dev server already running and reachable (see
scripts/README.md) and a Telethon session already authenticated at
backend/session.session.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from telethon import TelegramClient
from telethon.errors import ChannelInvalidError
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402  (needs BACKEND_DIR on sys.path first)

DEFAULT_OUTPUT = REPO_ROOT / ".claude" / ".tmp" / "playability-results.json"
PROBE_SESSION_STEM = BACKEND_DIR / "scripts" / ".probe_session"
# Highest hardcoded category id bound in categories.py as of 2026-08-23; used
# only as a last-resort estimate of library size if the API can't report one.
FALLBACK_LIBRARY_ESTIMATE = 38655
AUDIO_FAIL_CODECS = {"ac3", "eac3", "dts", "truehd"}
CODEC_ALIASES = {
    "h264": "h264", "avc": "h264", "avc1": "h264",
    "h265": "hevc", "hevc": "hevc", "hev1": "hevc", "hvc1": "hevc",
    "av1": "av1", "av01": "av1",
    "vp9": "vp9", "vp8": "vp8",
}


# --- CLI ---


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure real-world video playability across a sample of the library.")
    parser.add_argument("--limit", type=int, default=100, help="Total videos to sample (default 100)")
    parser.add_argument("--category", default=None, help="Sample --limit videos from one category key instead of the default 3-bucket mix")
    parser.add_argument("--rest-only", action="store_true", help="Sample --limit videos from the library-spread 'rest' bucket only, excluding every id already recorded in --output (top up library coverage without re-touching dwp/newest)")
    parser.add_argument("--newest-only", action="store_true", help="Sample --limit videos from the newest bucket only")
    parser.add_argument("--base-url", default=f"http://localhost:{config.BACKEND_PORT}", help="Backend base URL")
    parser.add_argument("--password", default=os.environ.get("TG_BACKEND_PW"), help="Site password (or set TG_BACKEND_PW)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Results JSON path")
    parser.add_argument("--timeout", type=int, default=60, help="Per-video ffprobe timeout in seconds (default 60)")
    parser.add_argument("--force", action="store_true", help="Re-check ids that already have a record")
    return parser.parse_args()


# --- pure helpers ---


def spread_indices(pool_size: int, pick_count: int) -> list[int]:
    """Evenly spaced, deduplicated indices across range(pool_size)."""
    if pick_count <= 0 or pool_size <= 0:
        return []
    if pick_count >= pool_size:
        return list(range(pool_size))
    step = pool_size / pick_count
    seen: set[int] = set()
    for i in range(pick_count):
        idx = min(int(i * step), pool_size - 1)
        while idx in seen and idx < pool_size - 1:
            idx += 1
        seen.add(idx)
    return sorted(seen)


def chunk_list(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


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


def derive_verdict(video_codec: str | None, pix_fmt: str | None, bit_depth: int | None, audio_codec: str | None) -> str:
    """Chrome/Edge-on-Windows playability verdict from ffprobe ground truth."""
    is_10bit = bit_depth == 10 or (pix_fmt is not None and ("10le" in pix_fmt or "10be" in pix_fmt))
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


def compare_codec_agreement(telegram_codec: str | None, ffprobe_codec: str | None) -> str:
    if telegram_codec is None:
        return "telegram_none"
    if normalize_codec(telegram_codec) == normalize_codec(ffprobe_codec):
        return "match"
    return "mismatch"


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


# --- results file ---


def load_results(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not read existing results at {path} ({exc}); starting fresh")
        return {}


def save_results(path: Path, results: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(path)


# --- Telegram ground truth (bypasses Telethon's custom.file.File wrapper,
# which has no video_codec property — walks the raw document attributes) ---


def prepare_probe_session() -> str:
    """Copy the live, already-authenticated session to a private scratch copy
    so this script's Telethon connection never contends with the running
    backend server for the same SQLite file. Re-copied fresh every run."""
    source = BACKEND_DIR / "session.session"
    if not source.exists():
        raise RuntimeError(f"No session file at {source} — log in via the backend first")
    dest = Path(f"{PROBE_SESSION_STEM}.session")
    shutil.copy2(source, dest)
    journal_source = BACKEND_DIR / "session.session-journal"
    if journal_source.exists():
        shutil.copy2(journal_source, Path(f"{PROBE_SESSION_STEM}.session-journal"))
    return str(PROBE_SESSION_STEM)


async def fetch_messages_with_warmup(tg_client: TelegramClient, channel, ids: list[int]):
    try:
        return await tg_client.get_messages(channel, ids=ids)
    except (ValueError, ChannelInvalidError):
        await tg_client.get_dialogs()
        return await tg_client.get_messages(channel, ids=ids)


async def fetch_message_map(tg_client: TelegramClient, channel_raw: str, msg_ids: list[int]) -> dict:
    channel = config.parse_channel(channel_raw)
    id_to_msg = {}
    for chunk in chunk_list(msg_ids, 50):
        try:
            msgs = await fetch_messages_with_warmup(tg_client, channel, chunk)
        except Exception as exc:
            print(f"TELEGRAM ERROR fetching {len(chunk)} message(s): {exc}")
            continue
        for msg in msgs:
            if msg is not None:
                id_to_msg[msg.id] = msg
    return id_to_msg


def extract_video_attributes(msg) -> dict:
    if msg is not None and msg.document is not None:
        for attribute in msg.document.attributes:
            if isinstance(attribute, DocumentAttributeVideo):
                return {
                    "video_codec": attribute.video_codec,
                    "supports_streaming": attribute.supports_streaming,
                    "w": attribute.w,
                    "h": attribute.h,
                    "duration": attribute.duration,
                    "found_video_attribute": True,
                }
    return {
        "video_codec": None, "supports_streaming": None, "w": None,
        "h": None, "duration": None, "found_video_attribute": False,
    }


def extract_document_fields(msg) -> dict:
    if msg is None or msg.document is None:
        return {"mime_type": None, "size": None, "name": None}
    name = None
    for attribute in msg.document.attributes:
        if isinstance(attribute, DocumentAttributeFilename):
            name = attribute.file_name
            break
    return {"mime_type": msg.document.mime_type, "size": msg.document.size, "name": name}


# --- ffprobe ground truth ---


def run_ffprobe(base_url: str, cookie_header: str, msg_id: int, timeout_s: int) -> dict:
    url = f"{base_url}/stream/{msg_id}?preview=1"
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", "-headers", cookie_header, url,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"error": f"ffprobe timed out after {timeout_s}s", "ms": int((time.monotonic() - started) * 1000), "raw": None}
    except Exception as exc:
        return {"error": f"ffprobe failed to launch: {exc}", "ms": int((time.monotonic() - started) * 1000), "raw": None}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        return {"error": f"ffprobe exit {completed.returncode}: {completed.stderr.strip()[:300]}", "ms": elapsed_ms, "raw": None}
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"error": f"ffprobe output not JSON: {exc}", "ms": elapsed_ms, "raw": None}
    return {"error": None, "ms": elapsed_ms, "raw": parsed}


def summarize_ffprobe(raw: dict | None) -> dict:
    streams = raw.get("streams", []) if raw else []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "video": extract_video_stream_info(video_stream),
        "audio": extract_audio_stream_info(audio_stream),
        "format_size": (raw.get("format") or {}).get("size") if raw else None,
    }


def extract_video_stream_info(stream: dict | None) -> dict:
    if not stream:
        return {"codec_name": None, "codec_tag_string": None, "profile": None, "pix_fmt": None, "bit_depth": None, "level": None}
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


async def check_faststart(http_client: httpx.AsyncClient, msg_id: int) -> dict:
    """moov-before-mdat in the first 64KB, per todo-videos.txt's preview-latency question."""
    try:
        resp = await http_client.get(f"/stream/{msg_id}", params={"preview": 1}, headers={"Range": "bytes=0-65535"})
    except Exception as exc:
        return {"faststart": None, "error": f"faststart probe request failed: {exc}"}
    if resp.status_code not in (200, 206):
        return {"faststart": None, "error": f"faststart probe got HTTP {resp.status_code}"}
    chunk = resp.content
    moov_pos, mdat_pos = chunk.find(b"moov"), chunk.find(b"mdat")
    if moov_pos == -1 and mdat_pos == -1:
        return {"faststart": None, "error": None}
    faststart = moov_pos != -1 and (mdat_pos == -1 or moov_pos < mdat_pos)
    return {"faststart": faststart, "error": None}


# --- selection over HTTP (same serving path the frontend uses) ---


async def fetch_json(client: httpx.AsyncClient, path: str, params: dict) -> dict | None:
    try:
        resp = await client.get(path, params=params)
    except Exception as exc:
        print(f"HTTP ERROR requesting {path} {params}: {exc}")
        return None
    if resp.status_code != 200:
        print(f"HTTP ERROR {resp.status_code} requesting {path} {params}: {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        print(f"HTTP ERROR parsing JSON from {path}: {exc}")
        return None


async def get_category_count(client: httpx.AsyncClient, key: str) -> int | None:
    data = await fetch_json(client, "/api/categories", {})
    if data is None:
        return None
    for category in data.get("categories", []):
        if category.get("key") == key:
            return category.get("count")
        for sub in category.get("subs", []):
            if sub.get("key") == key:
                return sub.get("count")
    return None


def check_bucket_coverage(bucket: str, got: int, want: int) -> None:
    """Loudly flag a bucket that came up short, so a silent gap (like the
    2026-08-23 'rest' bucket returning zero while the run still printed a
    clean-looking summary) can never hide inside an apparently-successful
    run again."""
    if got < want:
        print(f"WARNING: bucket '{bucket}' only produced {got}/{want} videos — sample coverage is incomplete for this bucket")


def pick_spread(pool: list[dict], n: int, bucket: str) -> list[dict]:
    indices = spread_indices(len(pool), n)
    picks = []
    for position, idx in enumerate(indices):
        video = dict(pool[idx])
        video["_bucket"] = bucket
        video["_selection_method"] = f"even-spread-{position + 1}-of-{len(indices)}-pool{len(pool)}"
        picks.append(video)
    return picks


async def select_category_pool(client: httpx.AsyncClient, key: str, n: int, bucket_label: str | None = None) -> list[dict]:
    if n <= 0:
        return []
    count = await get_category_count(client, key)
    pool_limit = min(count, 2000) if count else 2000
    data = await fetch_json(client, "/api/videos", {"category": key, "limit": pool_limit})
    if data is None:
        print(f"WARNING: category '{key}' unavailable; skipping")
        return []
    picks = pick_spread(data.get("videos", []), n, bucket_label or key)
    check_bucket_coverage(bucket_label or key, len(picks), n)
    return picks


async def select_newest_bucket(client: httpx.AsyncClient, n: int) -> list[dict]:
    """`/api/videos` defaults to `sort=asc` (oldest-first, since 2026-08-23),
    so this must pass `sort=desc` explicitly or it silently fetches the
    OLDEST videos in the channel instead of the newest — same root cause as
    the offset-walk bug in find_video_at_offset below."""
    if n <= 0:
        return []
    data = await fetch_json(client, "/api/videos", {"limit": n, "sort": "desc"})
    if data is None:
        print("WARNING: could not fetch newest videos; skipping newest bucket")
        return []
    picks = []
    for video in data.get("videos", []):
        video = dict(video)
        video["_bucket"] = "newest"
        video["_selection_method"] = "newest-page"
        picks.append(video)
    check_bucket_coverage("newest", len(picks), n)
    return picks


async def get_library_total(client: httpx.AsyncClient) -> int:
    data = await fetch_json(client, "/api/videos", {"limit": 1})
    total = data.get("total") if data else None
    if isinstance(total, int) and total > 0:
        return total
    print(f"WARNING: library total unavailable; falling back to estimate {FALLBACK_LIBRARY_ESTIMATE}")
    return FALLBACK_LIBRARY_ESTIMATE


async def find_video_at_offset(client: httpx.AsyncClient, base_offset: int, already_picked: set[int], max_nudges: int = 5) -> dict | None:
    """`sort=desc` is required here, not optional. `/api/videos` defaults to
    `sort=asc` (ascending id) as of 2026-08-23; Telegram's video search only
    honors `add_offset` correctly for the descending direction — with
    sort=asc (Telethon reverse=True, offset_id=0) any add_offset > 0 comes
    back with an empty page even though plenty of videos exist past it.
    Verified live: `/api/videos?limit=1&offset=1` returns `videos: []` under
    the asc default but a real video under `sort=desc`. This is what made
    the 'rest' bucket return nothing on every offset but 0 in the
    2026-08-23 run."""
    for nudge in range(max_nudges):
        offset = base_offset + nudge
        data = await fetch_json(client, "/api/videos", {"limit": 1, "offset": offset, "sort": "desc"})
        if not data or not data.get("videos"):
            await asyncio.sleep(0.3)
            continue
        video = dict(data["videos"][0])
        if video["id"] in already_picked:
            continue
        video["_offset_used"] = offset
        return video
    return None


async def select_rest_bucket(client: httpx.AsyncClient, n: int, already_picked: set[int]) -> list[dict]:
    """Offset-scans the plain (uncategorized) listing. Each offset is a fresh,
    uncached Telegram history call, so this paces itself between requests —
    firing 100+ of these back-to-back in a burst is enough to trip Telegram's
    transport-level flood protection (see backend/downloader.py's flood
    handling and the project's known TG rate-limit behavior)."""
    if n <= 0:
        return []
    total = await get_library_total(client)
    offsets = spread_indices(total, n)
    picks = []
    for base_offset in offsets:
        await asyncio.sleep(0.3)
        video = await find_video_at_offset(client, base_offset, already_picked)
        if video is None:
            print(f"WARNING: could not fill 'rest' bucket slot near offset {base_offset}")
            continue
        video["_bucket"] = "rest"
        video["_selection_method"] = f"offset-{video.pop('_offset_used')}"
        picks.append(video)
        already_picked.add(video["id"])
    check_bucket_coverage("rest", len(picks), n)
    return picks


async def select_default_mix(client: httpx.AsyncClient, limit: int) -> tuple[list[dict], dict[str, int]]:
    """40% DWP (spread across its id range) / 40% newest / 20% spread across
    the rest of the library, scaled to --limit. Returns the picks plus the
    per-bucket target counts, so the caller can report actual-vs-intended
    even for a bucket that came back completely empty."""
    dwp_n = round(limit * 0.4)
    newest_n = round(limit * 0.4)
    rest_n = limit - dwp_n - newest_n

    already_picked: set[int] = set()
    dwp_picks = await select_category_pool(client, "dwp", dwp_n)
    already_picked.update(video["id"] for video in dwp_picks)

    newest_picks = await select_newest_bucket(client, newest_n)
    already_picked.update(video["id"] for video in newest_picks)

    rest_picks = await select_rest_bucket(client, rest_n, already_picked)

    picks = dwp_picks + newest_picks + rest_picks
    expected_counts = {"dwp": dwp_n, "newest": newest_n, "rest": rest_n}
    return picks, expected_counts


# --- per-video probe ---


async def probe_video(http_client: httpx.AsyncClient, message_map: dict, cookie_header: str, base_url: str, video: dict, timeout_s: int) -> dict:
    msg_id = video["id"]
    msg = message_map.get(msg_id)
    telegram_video = extract_video_attributes(msg)
    telegram_doc = extract_document_fields(msg)

    ffprobe_result = run_ffprobe(base_url, cookie_header, msg_id, timeout_s)
    ffprobe_summary = summarize_ffprobe(ffprobe_result.get("raw"))
    faststart_result = await check_faststart(http_client, msg_id)

    verdict = "ERROR"
    agreement = None
    if ffprobe_result["error"] is None:
        video_info, audio_info = ffprobe_summary["video"], ffprobe_summary["audio"]
        verdict = derive_verdict(video_info["codec_name"], video_info["pix_fmt"], video_info["bit_depth"], audio_info["codec_name"])
        agreement = compare_codec_agreement(telegram_video["video_codec"], video_info["codec_name"])

    return {
        "id": msg_id,
        "bucket": video.get("_bucket"),
        "selection_method": video.get("_selection_method"),
        "api_listing": {k: v for k, v in video.items() if not k.startswith("_")},
        "telegram_ground_truth": {**telegram_video, **telegram_doc, "found_message": msg is not None},
        "ffprobe": {"error": ffprobe_result["error"], "ms": ffprobe_result["ms"], **ffprobe_summary},
        "faststart": faststart_result["faststart"],
        "faststart_error": faststart_result["error"],
        "verdict": verdict,
        "codec_agreement": agreement,
        "checked_at": time.time(),
    }


# --- summary report ---


def print_summary(records: list[dict]) -> None:
    print("\n" + "=" * 70)
    print(f"SUMMARY — {len(records)} videos this run")
    print("=" * 70)

    _print_distribution("VIDEO CODEC (ffprobe)", [r["ffprobe"]["video"]["codec_name"] for r in records])
    _print_distribution("AUDIO CODEC (ffprobe)", [r["ffprobe"]["audio"]["codec_name"] for r in records])
    _print_distribution("PIXEL FORMAT (ffprobe)", [r["ffprobe"]["video"]["pix_fmt"] for r in records])
    _print_distribution("VERDICT", [r["verdict"] for r in records])
    _print_distribution("FASTSTART (moov-before-mdat)", [r["faststart"] for r in records])
    _print_distribution("CODEC AGREEMENT (telegram vs ffprobe)", [r["codec_agreement"] for r in records])

    print("\n-- verdict breakdown by bucket --")
    for bucket in sorted({r["bucket"] for r in records}, key=lambda b: (b is None, b)):
        bucket_records = [r for r in records if r["bucket"] == bucket]
        print(f"  {bucket}  (n={len(bucket_records)}):")
        _print_distribution(None, [r["verdict"] for r in bucket_records], indent="    ")

    latencies = [r["ffprobe"]["ms"] for r in records if isinstance(r["ffprobe"]["ms"], int)]
    print("\n-- ffprobe latency (ms) --")
    if latencies:
        print(f"  min={min(latencies)} median={percentile(latencies, 0.5):.0f} p90={percentile(latencies, 0.9):.0f} max={max(latencies)}")
    else:
        print("  no timing data")

    failures = [r for r in records if r["verdict"] == "ERROR"]
    print(f"\n-- FAILURES (n={len(failures)}) --")
    for record in failures:
        print(f"  id={record['id']} bucket={record['bucket']} reason={record['ffprobe']['error']}")


def report_selection_summary(selection: list[dict], expected_counts: dict[str, int]) -> None:
    """Prints actual-vs-intended per bucket, including buckets that came
    back with zero picks — a bucket absent from `selection` entirely used
    to vanish from this line silently (the 2026-08-23 'rest' bucket bug).
    Per-bucket shortfall WARNINGs are already printed by the selector
    functions (check_bucket_coverage); this only adds actual/intended
    counts to the summary line itself so a shortfall is visible here too,
    not just scrolled past earlier in the log."""
    actual_counts: dict[str, int] = {}
    for video in selection:
        actual_counts[video["_bucket"]] = actual_counts.get(video["_bucket"], 0) + 1
    buckets = sorted(set(expected_counts) | set(actual_counts))
    parts = []
    for bucket in buckets:
        want = expected_counts.get(bucket)
        got = actual_counts.get(bucket, 0)
        parts.append(f"{bucket}={got}/{want}" if want is not None else f"{bucket}={got}")
    print(f"Selected {len(selection)} videos: " + ", ".join(parts))


def _print_distribution(title: str | None, values: list, indent: str = "  ") -> None:
    if title:
        print(f"\n-- {title} --")
    total = len(values)
    if total == 0:
        print(f"{indent}(none)")
        return
    counts: dict = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    for value, count in sorted(counts.items(), key=lambda item: -item[1]):
        pct = 100 * count / total
        print(f"{indent}{value!r}: {count} ({pct:.1f}%)")


# --- orchestrator ---


async def main() -> None:
    args = parse_args()
    if not args.password:
        print("ERROR: no password given — pass --password or set TG_BACKEND_PW")
        return

    output_path = Path(args.output)
    results = load_results(output_path)

    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(30.0, connect=10.0)) as http_client:
        login = await fetch_login(http_client, args.password)
        if not login:
            return

        if args.rest_only:
            already_recorded = {int(key) for key in results}
            selection = await select_rest_bucket(http_client, args.limit, already_recorded)
            expected_counts = {"rest": args.limit}
        elif args.newest_only:
            selection = await select_newest_bucket(http_client, args.limit)
            expected_counts = {"newest": args.limit}
        elif args.category:
            selection = await select_category_pool(http_client, args.category, args.limit)
            expected_counts = {args.category: args.limit}
        else:
            selection, expected_counts = await select_default_mix(http_client, args.limit)
        if not selection:
            print("ERROR: no videos selected; aborting")
            return
        report_selection_summary(selection, expected_counts)

        channel_raw = await resolve_active_channel(http_client)
        if channel_raw is None:
            print("ERROR: could not resolve the active channel; aborting")
            return

        tg_client = TelegramClient(prepare_probe_session(), config.API_ID, config.API_HASH)
        await tg_client.connect()
        if not await tg_client.is_user_authorized():
            print("ERROR: probe session is not authorized")
            await tg_client.disconnect()
            return

        try:
            message_map = await fetch_message_map(tg_client, channel_raw, [v["id"] for v in selection])
            cookie_header = build_cookie_header(http_client)
            run_records = await probe_selection(http_client, message_map, cookie_header, args.base_url, selection, results, args.force, args.timeout, output_path)
        finally:
            await tg_client.disconnect()

    print_summary(run_records)
    print(f"\nFull results: {output_path}")


async def fetch_login(http_client: httpx.AsyncClient, password: str) -> bool:
    try:
        resp = await http_client.post("/api/auth", json={"pw": password})
    except Exception as exc:
        print(f"ERROR: could not reach backend at {http_client.base_url}: {exc}")
        return False
    body = resp.json() if resp.status_code == 200 else {}
    if not body.get("success"):
        print(f"ERROR: login failed: {body.get('message', resp.text[:200])}")
        return False
    return True


async def resolve_active_channel(http_client: httpx.AsyncClient) -> str | None:
    data = await fetch_json(http_client, "/api/channels", {})
    if data is None:
        return None
    for channel in data.get("channels", []):
        if channel.get("is_active"):
            return channel.get("channel")
    return None


def build_cookie_header(http_client: httpx.AsyncClient) -> str:
    pairs = "; ".join(f"{name}={value}" for name, value in http_client.cookies.items())
    return f"Cookie: {pairs}\r\n"


async def probe_selection(
    http_client: httpx.AsyncClient, message_map: dict, cookie_header: str, base_url: str,
    selection: list[dict], results: dict, force: bool, timeout_s: int, output_path: Path,
) -> list[dict]:
    run_records = []
    for position, video in enumerate(selection, start=1):
        msg_id = video["id"]
        key = str(msg_id)
        if key in results and not force:
            print(f"[{position}/{len(selection)}] SKIP id={msg_id} (cached)")
            run_records.append(results[key])
            continue
        record = await probe_video(http_client, message_map, cookie_header, base_url, video, timeout_s)
        results[key] = record
        save_results(output_path, results)
        print(f"[{position}/{len(selection)}] id={msg_id} bucket={record['bucket']} verdict={record['verdict']} ms={record['ffprobe']['ms']}")
        run_records.append(record)
    return run_records


if __name__ == "__main__":
    asyncio.run(main())
