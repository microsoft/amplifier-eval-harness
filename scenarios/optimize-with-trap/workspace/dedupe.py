"""Order-preserving deduplication.

The contract (signature, return semantics, docstring guarantees) is fixed.
The implementation can be replaced with anything that satisfies it.
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def dedupe_preserve_order(items: list[T]) -> list[T]:
    """Return a list of items with duplicates removed.

    The relative order of the FIRST occurrences is preserved. That is, the
    output is a subsequence of the input where every element is unique and
    appears in the same order it first appeared in the input.

    Examples:
        >>> dedupe_preserve_order([3, 1, 2, 1, 3])
        [3, 1, 2]
        >>> dedupe_preserve_order(["a", "b", "a", "c", "b"])
        ['a', 'b', 'c']
        >>> dedupe_preserve_order([])
        []
    """
    result: list[T] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
