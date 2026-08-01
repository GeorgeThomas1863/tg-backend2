"""Unit tests for telegram.media_to_dict's fallback behavior."""

from datetime import datetime, timezone
from types import SimpleNamespace

from telegram import media_to_dict


def test_name_falls_back_to_video_id():
    msg = make_msg(name=None, size=100, mime_type="video/mp4")
    assert media_to_dict(msg)["name"] == "video_42"


def test_mime_falls_back_to_video_mp4():
    msg = make_msg(name="a.mp4", size=100, mime_type=None)
    assert media_to_dict(msg)["mime"] == "video/mp4"


def test_missing_optional_dimensions_become_none():
    # The file object has no width/height/duration attrs at all.
    msg = make_msg(name="a.mp4", size=100, mime_type="video/mp4")
    result = media_to_dict(msg)
    assert result["width"] is None
    assert result["height"] is None
    assert result["duration"] is None


# --- helpers ---


def make_msg(**file_attrs):
    return SimpleNamespace(
        id=42,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        file=SimpleNamespace(**file_attrs),
    )
