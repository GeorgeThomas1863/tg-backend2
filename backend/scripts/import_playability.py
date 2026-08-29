"""
Playability results importer.

Reads the JSON file check_playability.py writes (a dict keyed by video id)
and bulk-upserts one doc per record into the "playability" Mongo collection,
so playability verdicts can be queried/joined without re-running the probe.

This is a one-shot seeding tool, not part of the served app: it does not
change how videos are served. backend/playability.py reads the collection to
attach verdicts to /api/videos pages.

Exits non-zero when the results file can't be loaded or the Mongo upsert
fails, so callers can tell a failed run from a legitimately empty one.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from pymongo import AsyncMongoClient, UpdateOne

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402  (needs BACKEND_DIR on sys.path first)

DEFAULT_RESULTS_PATH = REPO_ROOT / ".claude" / ".tmp" / "playability-results.json"
COLLECTION_NAME = "playability"


# --- CLI ---


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import check_playability.py results into MongoDB.")
    parser.add_argument("--file", default=str(DEFAULT_RESULTS_PATH), help="Results JSON path")
    return parser.parse_args()


# --- pure helpers (testable without Mongo) ---


def load_results(path: Path) -> dict | None:
    """Load the results JSON file. Returns None (already logged) if it's
    missing, unreadable, or not a JSON object."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not read results file {path}: {exc}")
        return None
    if not isinstance(parsed, dict):
        print(f"ERROR: results file {path} is not a JSON object")
        return None
    return parsed


def record_to_doc(msg_id_key: str, record, now_ts: int) -> dict | None:
    """Map one (id, record) pair from the results file to a playability
    Mongo doc. Returns None if the id can't be parsed or the record has no
    usable verdict."""
    msg_id = _parse_msg_id(msg_id_key)
    if msg_id is None:
        return None
    if not isinstance(record, dict):
        return None
    verdict = record.get("verdict")
    if not isinstance(verdict, str) or not verdict:
        return None

    ffprobe = record.get("ffprobe")
    video = ffprobe.get("video") if isinstance(ffprobe, dict) else None
    audio = ffprobe.get("audio") if isinstance(ffprobe, dict) else None

    return {
        "_id": msg_id,
        "verdict": verdict,
        "video_codec": _as_codec(video),
        "audio_codec": _as_codec(audio),
        "faststart": record.get("faststart") if isinstance(record.get("faststart"), bool) else None,
        "updated_ts": now_ts,
    }


def build_docs(results: dict, now_ts: int) -> tuple[list[dict], int]:
    """Map every record in results to a doc. Returns (docs, skipped_count)."""
    docs = []
    skipped = 0
    for msg_id_key, record in results.items():
        doc = record_to_doc(msg_id_key, record, now_ts)
        if doc is None:
            skipped += 1
            continue
        docs.append(doc)
    return docs, skipped


def _parse_msg_id(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _as_codec(stream) -> str | None:
    if not isinstance(stream, dict):
        return None
    codec = stream.get("codec_name")
    return codec if isinstance(codec, str) else None


# --- Mongo ---


async def upsert_docs(docs: list[dict]) -> int | None:
    """Bulk-upsert playability docs by _id. Returns the number imported
    (upserted + matched), or None (already logged) on failure."""
    if not docs:
        return 0
    operations = [_doc_to_upsert(doc) for doc in docs]
    client = AsyncMongoClient(config.MONGO_URI)
    try:
        collection = client[config.DB_NAME][COLLECTION_NAME]
        result = await collection.bulk_write(operations, ordered=False)
        return result.upserted_count + result.matched_count
    except Exception as exc:
        print(f"ERROR: Mongo bulk upsert failed: {exc}")
        return None
    finally:
        await client.close()


def _doc_to_upsert(doc: dict) -> UpdateOne:
    # _id stays out of $set — Mongo forbids modifying it — and the upsert
    # path copies _id from the filter into the inserted doc.
    fields = {key: value for key, value in doc.items() if key != "_id"}
    return UpdateOne({"_id": doc["_id"]}, {"$set": fields}, upsert=True)


# --- orchestrator ---


async def main() -> int:
    args = parse_args()
    now_ts = int(time.time())
    results = load_results(Path(args.file))
    if results is None:
        return 1
    docs, skipped = build_docs(results, now_ts)
    imported = await upsert_docs(docs)
    if imported is None:
        return 1
    print(f"Imported {imported}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
