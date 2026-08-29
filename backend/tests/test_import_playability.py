"""Unit tests for import_playability.py's pure helpers and exit codes (no live Mongo)."""

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import import_playability  # noqa: E402
from import_playability import build_docs, load_results, record_to_doc  # noqa: E402

NOW_TS = 1787536743


def make_record(**overrides) -> dict:
    record = {
        "id": 35337,
        "verdict": "UNKNOWN_VIDEO_mpeg4",
        "faststart": False,
        "ffprobe": {
            "video": {"codec_name": "mpeg4"},
            "audio": {"codec_name": "aac"},
        },
    }
    record.update(overrides)
    return record


def test_good_record_maps_to_expected_doc():
    doc = record_to_doc("35337", make_record(), NOW_TS)
    assert doc == {
        "_id": 35337,
        "verdict": "UNKNOWN_VIDEO_mpeg4",
        "video_codec": "mpeg4",
        "audio_codec": "aac",
        "faststart": False,
        "updated_ts": NOW_TS,
    }


def test_verdict_passes_through_unchanged():
    doc = record_to_doc("1", make_record(verdict="PLAYS"), NOW_TS)
    assert doc["verdict"] == "PLAYS"

    doc = record_to_doc("2", make_record(verdict="FAILS_10BIT"), NOW_TS)
    assert doc["verdict"] == "FAILS_10BIT"


def test_missing_verdict_is_malformed():
    record = make_record()
    del record["verdict"]
    assert record_to_doc("35337", record, NOW_TS) is None


def test_non_string_verdict_is_malformed():
    assert record_to_doc("35337", make_record(verdict=None), NOW_TS) is None


def test_unparseable_id_is_malformed():
    assert record_to_doc("not-an-id", make_record(), NOW_TS) is None


def test_non_dict_record_is_malformed():
    assert record_to_doc("35337", "not a dict", NOW_TS) is None


def test_missing_ffprobe_section_defaults_codecs_to_none():
    record = make_record()
    del record["ffprobe"]
    doc = record_to_doc("35337", record, NOW_TS)
    assert doc["video_codec"] is None
    assert doc["audio_codec"] is None


def test_non_bool_faststart_defaults_to_none():
    doc = record_to_doc("35337", make_record(faststart=None), NOW_TS)
    assert doc["faststart"] is None


def test_build_docs_counts_skipped_and_collects_valid_docs():
    results = {
        "35337": make_record(id=35337),
        "bad": {"verdict": None},
        "999": make_record(id=999, verdict="PLAYS"),
    }
    docs, skipped = build_docs(results, NOW_TS)
    assert skipped == 1
    assert sorted(doc["_id"] for doc in docs) == [999, 35337]


# --- load_results: failure vs legitimately empty ---


def test_load_results_returns_none_for_missing_file(tmp_path):
    assert load_results(tmp_path / "missing.json") is None


def test_load_results_returns_none_for_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_results(path) is None


def test_load_results_returns_none_for_non_object_json(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_results(path) is None


def test_load_results_returns_empty_dict_for_empty_object(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("{}", encoding="utf-8")
    assert load_results(path) == {}


# --- main exit codes ---


def _point_argv_at(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(sys, "argv", ["import_playability.py", "--file", str(path)])


async def test_main_exits_nonzero_when_results_file_unreadable(tmp_path, monkeypatch):
    _point_argv_at(monkeypatch, tmp_path / "missing.json")

    assert await import_playability.main() == 1


async def test_main_exits_nonzero_on_mongo_failure(tmp_path, monkeypatch):
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"35337": make_record()}), encoding="utf-8")
    _point_argv_at(monkeypatch, path)

    async def fake_upsert_docs(docs):
        return None

    monkeypatch.setattr(import_playability, "upsert_docs", fake_upsert_docs)

    assert await import_playability.main() == 1


async def test_main_exits_zero_on_successful_import(tmp_path, monkeypatch):
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"35337": make_record()}), encoding="utf-8")
    _point_argv_at(monkeypatch, path)

    async def fake_upsert_docs(docs):
        return len(docs)

    monkeypatch.setattr(import_playability, "upsert_docs", fake_upsert_docs)

    assert await import_playability.main() == 0
