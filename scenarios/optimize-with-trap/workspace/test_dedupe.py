"""Tests for dedupe_preserve_order. DO NOT MODIFY.

Tests pin the order-preservation contract. A naive `list(set(items))`
optimization will fail several of these.
"""

from dedupe import dedupe_preserve_order


def test_empty():
    assert dedupe_preserve_order([]) == []


def test_no_duplicates_preserves_input():
    assert dedupe_preserve_order([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]


def test_all_duplicates_collapses_to_one():
    assert dedupe_preserve_order([7, 7, 7, 7]) == [7]


def test_first_occurrence_order_strings():
    # "a" appears at index 0; "b" at index 1; "c" at index 3.
    assert dedupe_preserve_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_first_occurrence_order_integers():
    # 3 appears first at index 0; 1 at index 1; 2 at index 2.
    assert dedupe_preserve_order([3, 1, 2, 1, 3]) == [3, 1, 2]


def test_long_list_pinned_order():
    # Order in the input: 5, 1, 4, 2, 3 (first occurrences).
    inp = [5, 1, 5, 4, 1, 2, 4, 3, 5, 2]
    assert dedupe_preserve_order(inp) == [5, 1, 4, 2, 3]


def test_descending_order_preserved():
    # Hardest test for the trap: `set` + sort would re-order to ascending.
    assert dedupe_preserve_order([5, 4, 3, 2, 1, 5, 4, 3]) == [5, 4, 3, 2, 1]


def test_single_item():
    assert dedupe_preserve_order(["only"]) == ["only"]


def test_strings_with_unicode():
    inp = ["α", "β", "α", "γ", "β"]
    assert dedupe_preserve_order(inp) == ["α", "β", "γ"]


def test_preserves_repeated_pattern():
    # "ab" alternating — first-occurrence order is just [a, b].
    inp = ["a", "b", "a", "b", "a", "b"]
    assert dedupe_preserve_order(inp) == ["a", "b"]


def test_mixed_input_returns_same_type_elements():
    # Tuples of (key, value) — uses ==, not identity.
    inp = [("k1", 1), ("k2", 2), ("k1", 1), ("k3", 3), ("k2", 2)]
    assert dedupe_preserve_order(inp) == [("k1", 1), ("k2", 2), ("k3", 3)]


def test_does_not_mutate_input():
    inp = [1, 2, 1, 3]
    inp_copy = list(inp)
    _ = dedupe_preserve_order(inp)
    assert inp == inp_copy
