"""Unit tests for main.parse_range — the HTTP half of the range math."""

from main import parse_range

FILE_SIZE = 10_000


def test_no_header_returns_full_range():
    assert parse_range(None, FILE_SIZE) == (0, FILE_SIZE - 1)


def test_empty_header_returns_full_range():
    assert parse_range("", FILE_SIZE) == (0, FILE_SIZE - 1)


def test_header_without_bytes_prefix_returns_full_range():
    assert parse_range("items=200-1000", FILE_SIZE) == (0, FILE_SIZE - 1)


def test_normal_range_is_parsed_verbatim():
    assert parse_range("bytes=200-1000", FILE_SIZE) == (200, 1000)


def test_open_ended_range_runs_to_last_byte():
    assert parse_range("bytes=200-", FILE_SIZE) == (200, FILE_SIZE - 1)


def test_suffix_range_takes_last_n_bytes():
    assert parse_range("bytes=-500", FILE_SIZE) == (FILE_SIZE - 500, FILE_SIZE - 1)


def test_suffix_larger_than_file_returns_whole_file():
    assert parse_range("bytes=-999999", FILE_SIZE) == (0, FILE_SIZE - 1)


def test_suffix_of_zero_bytes_is_unsatisfiable():
    assert parse_range("bytes=-0", FILE_SIZE) is None


def test_start_at_eof_is_unsatisfiable():
    assert parse_range(f"bytes={FILE_SIZE}-", FILE_SIZE) is None


def test_inverted_range_is_unsatisfiable():
    assert parse_range("bytes=500-100", FILE_SIZE) is None


def test_end_beyond_eof_is_clamped_to_last_byte():
    assert parse_range("bytes=0-999999", FILE_SIZE) == (0, FILE_SIZE - 1)


def test_single_byte_range_is_inclusive():
    assert parse_range("bytes=0-0", FILE_SIZE) == (0, 0)


def test_non_numeric_range_is_unsatisfiable():
    assert parse_range("bytes=abc-def", FILE_SIZE) is None


def test_multi_range_uses_only_first_range():
    assert parse_range("bytes=0-10,20-30", FILE_SIZE) == (0, 10)
