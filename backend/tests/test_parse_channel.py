"""Unit tests for config.parse_channel — numeric channel IDs regressed once."""

from config import parse_channel


def test_negative_numeric_id_becomes_int():
    # THE regression: "-100..." IDs must come back as ints for Telethon.
    assert parse_channel("-1001234567890") == -1001234567890


def test_positive_numeric_id_becomes_int():
    assert parse_channel("1234567890") == 1234567890


def test_username_stays_a_string():
    assert parse_channel("mychannel") == "mychannel"


def test_username_whitespace_is_stripped():
    assert parse_channel("  mychannel \n") == "mychannel"


def test_at_prefixed_username_passes_through_unchanged():
    # Documents current behavior: no "@" normalisation happens.
    assert parse_channel("@name") == "@name"
