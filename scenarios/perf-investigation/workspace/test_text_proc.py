"""Correctness tests for count_unique_words. DO NOT MODIFY."""

import pytest

from text_proc import count_unique_words


def test_empty_string():
    assert count_unique_words("") == 0


def test_whitespace_only():
    assert count_unique_words("   \n\t  ") == 0


def test_simple():
    assert count_unique_words("the quick brown fox") == 4


def test_case_insensitive():
    assert count_unique_words("The the THE tHe") == 1


def test_repeated():
    assert count_unique_words("a b a b c a") == 3


@pytest.mark.parametrize(
    "text, expected",
    [
        ("one", 1),
        ("a a a a a a", 1),
        ("alpha beta gamma alpha beta", 3),
        ("hello, world! hello world", 4),  # punctuation makes "world!" != "world"
    ],
)
def test_examples(text, expected):
    assert count_unique_words(text) == expected
