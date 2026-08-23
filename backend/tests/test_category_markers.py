import copy
import json
from pathlib import Path

import categories
import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "postdata1_markers_tail.json"


def load_markers():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_prepare_table_rejects_open_base_sub():
    table = [{
        "name": "Major", "tag": "MAJOR", "start": 1, "end": None,
        "subs": [{"name": "Open sub", "start": 2, "end": None}],
    }]

    with pytest.raises(ValueError, match="Base-table subs must have an integer end"):
        categories._prepare_table(table)


def test_end_marker_after_seeded_open_major_closes_major():
    markers = [{
        "forwardFromMessageId": 40000,
        "text": "#RK2 #end",
    }]

    extended, ranges = categories._extend_table(markers)

    assert extended[-1]["end"] == 40000
    assert ranges["rk2"] == (35916, 40000)


def test_extends_open_base_major_from_fixture_tail():
    extended, ranges = categories._extend_table(load_markers())
    assert extended[:-1] == categories.CATEGORY_TABLE[:-1]
    assert extended[-1]["end"] is None
    assert len(extended[-1]["subs"]) == 13
    assert extended[-1]["subs"][-1] == {
        "name": "CrazyAsianGF", "start": 38656, "end": None,
        "key": "rk2-crazyasiangf", "parent": None,
    }
    assert ranges["rk2-crazyasiangf"] == (38656, None)


def test_parser_uses_prefix_overlap_noise_and_implicit_closes():
    markers = [
        {"forwardFromMessageId": 40000, "text": "!!! #Major #begin"},
        {"forwardFromMessageId": 40001, "text": "+++ #One #begin"},
        {"forwardFromMessageId": 40002, "text": "+++ #Two #begin"},
        {"forwardFromMessageId": 40003, "text": "."},
        {"forwardFromMessageId": 40004, "text": "#Two #begin"},
        {"forwardFromMessageId": 40005, "text": "#Different #end"},
        {"forwardFromMessageId": 40006, "text": "!!! #Major #begin"},
        {"forwardFromMessageId": 40007, "text": "!!! #Next #begin"},
    ]
    extended, _ = categories._extend_table(markers)
    major, duplicate, next_major = extended[-3:]
    assert [(sub["name"], sub["end"]) for sub in major["subs"]] == [
        ("One", 40002), ("Two", 40004)
    ]
    assert major["end"] == 40005
    assert duplicate["key"] == "major-2"
    assert duplicate["end"] == 40007
    assert next_major["end"] is None


def test_new_major_closes_reality_kings_and_avoids_key_collision():
    markers = [{"forwardFromMessageId": 40000, "text": "!!! #RK2 #begin"}]
    extended, ranges = categories._extend_table(markers)
    assert extended[-2]["end"] == 40000
    assert extended[-1]["key"] == "rk2-2"
    assert ranges["rk2"] == (35916, 40000)


def test_open_ranges_count_without_upper_bound_and_estimate_zero(monkeypatch):
    table, ranges = categories._extend_table(load_markers())
    monkeypatch.setattr(categories, "_table_state", (table, ranges))
    assert categories._count_ranges([38656, 38657, 40000])["rk2-crazyasiangf"] == 2
    assert categories._estimate_counts()["rk2-crazyasiangf"] == 0


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction):
        self.documents.sort(key=lambda item: item[field])
        return self

    async def to_list(self, length):
        return self.documents


class RefreshCollection:
    def __init__(self, markers, ids, error=None):
        self.markers = markers
        self.ids = ids
        self.error = error

    def find(self, query, projection):
        if self.error:
            raise self.error
        if query["paramType"] == "textParams":
            return FakeCursor(copy.deepcopy(self.markers))
        return FakeCursor([
            {"forwardFromMessageId": message_id} for message_id in self.ids
        ])


async def test_refresh_serializes_and_resolves_open_sub(monkeypatch):
    collection = RefreshCollection(load_markers(), [38656, 38657, 40000])
    monkeypatch.setattr(categories, "get_collection", lambda: collection)
    monkeypatch.setattr(categories, "_count_cache", None)
    monkeypatch.setattr(categories, "_count_cache_expires", 0.0)
    monkeypatch.setattr(
        categories, "_table_state", (categories.CATEGORY_TABLE, categories._base_ranges)
    )
    result = await categories.get_categories()
    reality_kings = result["categories"][-1]
    assert reality_kings["end"] == "?"
    assert reality_kings["subs"][-1]["end"] == "?"
    assert reality_kings["subs"][-1]["count"] == 2
    assert categories.resolve("rk2-crazyasiangf") == (38656, None)


async def test_refresh_failure_keeps_extended_table_and_exact_counts(
    monkeypatch,
):
    collection = RefreshCollection(load_markers(), [38657])
    monkeypatch.setattr(categories, "get_collection", lambda: collection)
    monkeypatch.setattr(categories, "_count_cache", None)
    monkeypatch.setattr(categories, "_count_cache_expires", 0.0)
    await categories.get_categories()
    monkeypatch.setattr(
        categories,
        "get_collection",
        lambda: RefreshCollection([], [], RuntimeError("offline")),
    )
    categories._count_cache_expires = 0.0
    result = await categories.get_categories()
    assert result["counts_exact"] is True
    assert result["categories"][-1]["subs"][-1]["name"] == "CrazyAsianGF"
    assert result["categories"][-1]["subs"][-1]["end"] == "?"
    assert result["categories"][-1]["subs"][-1]["count"] == 1
