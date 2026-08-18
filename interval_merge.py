"""Recursively merge overlapping or adjacent intervals.

Fixes an infinite-recursion bug that occurs for certain inputs (duplicate
intervals, zero-width intervals, or intervals that touch exactly at an
endpoint). A naive recursive merge that "merges in place" and then
recurses on the same index without shrinking the interval list never
makes progress on inputs like [(1, 5), (1, 5)] or [(2, 2), (2, 2)] and
recurses forever (eventually raising RecursionError).

This implementation guarantees every recursive call strictly reduces the
remaining work: it either advances the index (no merge needed) or shrinks
the interval list by one element (a merge happened). Either way the base
case is reached in a bounded number of steps. As an extra safeguard
against Python's recursion limit on very large inputs, it falls back to
an equivalent iterative implementation once the input size approaches
the current recursion limit.
"""

import sys
from typing import List, Tuple

Interval = Tuple[float, float]


def merge_intervals(intervals: List[Interval]) -> List[Interval]:
    """Merge overlapping/adjacent intervals into a minimal sorted list.

    Two intervals are merged if they overlap or touch, i.e. if
    ``next_start <= current_end``. Intervals are treated as closed
    ranges ``[start, end]``.
    """
    if not intervals:
        return []

    sorted_intervals = sorted(intervals, key=lambda iv: iv[0])

    # Guard against RecursionError on large inputs: the recursive merge
    # can recurse once per input interval in the worst case (no merges),
    # so fall back to the iterative version well before hitting the limit.
    if len(sorted_intervals) > sys.getrecursionlimit() - 50:
        return _merge_iterative(sorted_intervals)

    return _merge_recursive(sorted_intervals, 0)


def _merge_recursive(intervals: List[Interval], index: int) -> List[Interval]:
    # Base case: zero or one interval left starting at `index`.
    if index >= len(intervals) - 1:
        return intervals[index:]

    current_start, current_end = intervals[index]
    next_start, next_end = intervals[index + 1]

    if next_start <= current_end:
        # Overlap (or touch): collapse the two intervals into one and
        # recurse on the *shrunk* list. The list is strictly shorter
        # than before, so this always makes progress even when
        # current == next (duplicate intervals) or both are zero-width.
        merged = (current_start, max(current_end, next_end))
        shrunk = intervals[:index] + [merged] + intervals[index + 2:]
        return _merge_recursive(shrunk, index)

    # No overlap: this interval is final, advance to the next index.
    return [intervals[index]] + _merge_recursive(intervals, index + 1)


def _merge_iterative(intervals: List[Interval]) -> List[Interval]:
    result: List[Interval] = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = result[-1]
        if start <= last_end:
            result[-1] = (last_start, max(last_end, end))
        else:
            result.append((start, end))
    return result
