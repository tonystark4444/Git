import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interval_merge import merge_intervals


def test_empty():
    assert merge_intervals([]) == []


def test_single_interval():
    assert merge_intervals([(1, 5)]) == [(1, 5)]


def test_no_overlap():
    assert merge_intervals([(1, 2), (5, 6), (10, 12)]) == [(1, 2), (5, 6), (10, 12)]


def test_simple_overlap():
    assert merge_intervals([(1, 5), (3, 8)]) == [(1, 8)]


def test_unsorted_input_is_sorted_and_merged():
    assert merge_intervals([(5, 8), (1, 3)]) == [(1, 3), (5, 8)]


def test_touching_intervals_merge():
    # Endpoints touch exactly: treated as overlapping.
    assert merge_intervals([(1, 5), (5, 10)]) == [(1, 10)]


def test_duplicate_intervals_do_not_infinite_loop():
    # A naive "merge in place, recurse on same index" implementation
    # never advances on exact duplicates and recurses forever.
    assert merge_intervals([(1, 5), (1, 5), (1, 5)]) == [(1, 5)]


def test_zero_width_duplicate_intervals_do_not_infinite_loop():
    # Zero-width intervals that are identical are the other classic
    # trigger for the same infinite-recursion bug.
    assert merge_intervals([(2, 2), (2, 2), (2, 2)]) == [(2, 2)]


def test_chain_of_overlaps_collapses_to_one():
    assert merge_intervals([(1, 3), (2, 6), (5, 10), (15, 18)]) == [(1, 10), (15, 18)]


def test_large_input_uses_iterative_fallback_without_recursion_error():
    n = sys.getrecursionlimit() * 2
    intervals = [(i, i + 1) for i in range(0, n * 2, 2)]  # all disjoint
    result = merge_intervals(intervals)
    assert result == intervals


def test_large_input_with_many_duplicates_via_iterative_fallback():
    n = sys.getrecursionlimit() * 2
    intervals = [(1, 1)] * n
    assert merge_intervals(intervals) == [(1, 1)]
