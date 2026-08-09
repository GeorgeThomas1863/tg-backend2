"""Unit tests for the persistent channel registry."""

from unittest.mock import Mock

from bson import ObjectId
import pytest
from pymongo.errors import DuplicateKeyError
from telethon.tl.types import Channel

import channels
import telegram


class FakeResult:
    def __init__(self, inserted_id=None):
        self.inserted_id = inserted_id


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction):
        self.documents.sort(key=lambda document: document[field])
        return self

    async def to_list(self, length):
        return [document.copy() for document in self.documents]


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [document.copy() for document in documents or []]

    async def count_documents(self, query):
        return len(self.documents)

    async def find_one(self, query):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return document.copy()
        return None

    def find(self, query):
        return FakeCursor([document.copy() for document in self.documents])

    async def insert_one(self, document):
        if any(
            item["channel"].lower() == document["channel"].lower()
            for item in self.documents
        ):
            raise DuplicateKeyError("duplicate channel")
        stored = document.copy()
        stored.setdefault("_id", ObjectId())
        self.documents.append(stored)
        return FakeResult(stored["_id"])

    async def update_many(self, query, update):
        if isinstance(update, list):
            target_id = update[0]["$set"]["is_default"]["$eq"][1]
            for document in self.documents:
                document["is_default"] = document["_id"] == target_id
            return FakeResult()
        for document in self.documents:
            document.update(update["$set"])
        return FakeResult()

    async def create_index(self, field, **options):
        return f"{field}_1"

    async def update_one(self, query, update):
        for document in self.documents:
            if document["_id"] == query["_id"]:
                document.update(update["$set"])
        return FakeResult()

    async def delete_one(self, query):
        self.documents = [
            document for document in self.documents if document["_id"] != query["_id"]
        ]
        return FakeResult()


@pytest.fixture(autouse=True)
def reset_registry(monkeypatch):
    collection = FakeCollection()
    monkeypatch.setattr(channels, "get_collection", lambda: collection)
    monkeypatch.setattr(channels, "_active_channel", None)

    async def get_entity(channel):
        return Mock(spec=Channel, title=f"Title {channel}")

    monkeypatch.setattr(telegram.client, "get_entity", get_entity)
    return collection


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        (" @Some_Name ", "Some_Name"),
        ("https://t.me/Some_Name/", "Some_Name"),
        ("Some_Name", "Some_Name"),
        ("-1001706757504", "-1001706757504"),
    ],
)
async def test_add_channel_normalizes_supported_inputs(reset_registry, raw, normalized):
    result = await channels.add_channel(raw)

    assert result["success"] is True
    assert result["channel"]["channel"] == normalized


async def test_add_channel_rejects_normalized_duplicate(reset_registry):
    assert (await channels.add_channel("@Example"))["success"] is True

    result = await channels.add_channel("https://t.me/example")

    assert result == {"success": False, "message": "That channel is already saved"}
    assert len(reset_registry.documents) == 1


async def test_add_channel_rejects_non_channel_entity(reset_registry, monkeypatch):
    async def get_entity(channel):
        return Mock(title="Ordinary group")

    monkeypatch.setattr(telegram.client, "get_entity", get_entity)

    result = await channels.add_channel("ordinary_group")

    assert result["success"] is False
    assert reset_registry.documents == []


async def test_startup_seeds_empty_registry_from_environment(reset_registry, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@Seed_Channel")

    await channels.startup()

    assert len(reset_registry.documents) == 1
    assert reset_registry.documents[0]["is_default"] is True
    assert channels.get_active() == "Seed_Channel"
    assert channels.active_key() == "seed_channel"


async def test_startup_leaves_empty_registry_without_seed(reset_registry, monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHANNEL", raising=False)

    await channels.startup()

    assert reset_registry.documents == []
    assert channels.get_active() is None
    assert channels.active_key() is None


async def test_seed_uses_raw_title_when_telegram_resolution_fails(
    reset_registry, monkeypatch
):
    monkeypatch.setenv("TELEGRAM_CHANNEL", "seed_name")

    async def fail_resolution(channel):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(telegram.client, "get_entity", fail_resolution)
    await channels.startup()

    assert reset_registry.documents[0]["title"] == "seed_name"


async def test_set_default_keeps_exactly_one_default(reset_registry):
    first = await channels.add_channel("first")
    second = await channels.add_channel("second")

    result = await channels.set_default(second["channel"]["id"])

    assert result["success"] is True
    defaults = [item for item in reset_registry.documents if item["is_default"]]
    assert [str(item["_id"]) for item in defaults] == [second["channel"]["id"]]
    assert first["channel"]["id"] != second["channel"]["id"]


async def test_set_active_updates_runtime_accessors(reset_registry):
    await channels.add_channel("first")
    numeric = await channels.add_channel("-1001706757504")

    result = await channels.set_active(numeric["channel"]["id"])

    assert result["success"] is True
    assert channels.get_active() == -1001706757504
    assert channels.active_key() == "-1001706757504"

    listed = await channels.list_channels()
    active = [item for item in listed if item["is_active"]]
    assert [item["id"] for item in active] == [numeric["channel"]["id"]]


async def test_remove_blocks_default_and_active_channels(reset_registry):
    default = await channels.add_channel("default_channel")
    removable = await channels.add_channel("removable")
    await channels.set_active(removable["channel"]["id"])

    default_result = await channels.remove_channel(default["channel"]["id"])
    active_result = await channels.remove_channel(removable["channel"]["id"])

    assert default_result["success"] is False
    assert "default" in default_result["message"].lower()
    assert active_result["success"] is False
    assert "active" in active_result["message"].lower()


async def test_remove_deletes_unprotected_channel(reset_registry):
    first = await channels.add_channel("first")
    second = await channels.add_channel("second")
    await channels.set_active(first["channel"]["id"])

    result = await channels.remove_channel(second["channel"]["id"])

    assert result["success"] is True
    assert len(reset_registry.documents) == 1
