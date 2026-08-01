"""plan_blocks: inclusive byte range → per-block slices (end exclusive)."""

from streaming import BlockSlice, plan_blocks
from config import BLOCK_SIZE

FILE_SIZE = 3 * BLOCK_SIZE + 1000  # 4 blocks; last is 1000 bytes


def test_range_inside_one_block():
    assert plan_blocks(100, 199, FILE_SIZE) == [BlockSlice(0, 100, 200)]


def test_range_spanning_two_blocks():
    plans = plan_blocks(BLOCK_SIZE - 10, BLOCK_SIZE + 9, FILE_SIZE)
    assert plans == [
        BlockSlice(0, BLOCK_SIZE - 10, BLOCK_SIZE),
        BlockSlice(1, 0, 10),
    ]


def test_exact_block_boundaries():
    plans = plan_blocks(BLOCK_SIZE, 2 * BLOCK_SIZE - 1, FILE_SIZE)
    assert plans == [BlockSlice(1, 0, BLOCK_SIZE)]


def test_final_short_block_to_eof():
    plans = plan_blocks(3 * BLOCK_SIZE, FILE_SIZE - 1, FILE_SIZE)
    assert plans == [BlockSlice(3, 0, 1000)]


def test_end_clamped_to_file_size():
    plans = plan_blocks(3 * BLOCK_SIZE, 99 * BLOCK_SIZE, FILE_SIZE)
    assert plans == [BlockSlice(3, 0, 1000)]


def test_single_byte():
    assert plan_blocks(5, 5, FILE_SIZE) == [BlockSlice(0, 5, 6)]


def test_invalid_ranges_return_empty():
    assert plan_blocks(-1, 10, FILE_SIZE) == []
    assert plan_blocks(10, 9, FILE_SIZE) == []
    assert plan_blocks(FILE_SIZE, FILE_SIZE + 10, FILE_SIZE) == []
