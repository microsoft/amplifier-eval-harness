"""Tests for the CSV parser. DO NOT MODIFY."""

import pytest

from csv_parser import parse_csv


# ---- empty / trivial ------------------------------------------------------


def test_empty_input():
    assert parse_csv("") == []


def test_single_field_no_newline():
    assert parse_csv("hello") == [["hello"]]


def test_single_field_with_newline():
    assert parse_csv("hello\n") == [["hello"]]


# ---- basic shape ----------------------------------------------------------


def test_basic_two_rows():
    assert parse_csv("a,b\nc,d\n") == [["a", "b"], ["c", "d"]]


def test_no_trailing_newline():
    assert parse_csv("a,b\nc,d") == [["a", "b"], ["c", "d"]]


def test_three_columns():
    assert parse_csv("1,2,3\n4,5,6\n") == [["1", "2", "3"], ["4", "5", "6"]]


# ---- empty fields ---------------------------------------------------------


def test_empty_field_middle():
    assert parse_csv("a,,b") == [["a", "", "b"]]


def test_leading_empty():
    assert parse_csv(",a") == [["", "a"]]


def test_trailing_empty():
    assert parse_csv("a,") == [["a", ""]]


def test_all_empty_three_fields():
    assert parse_csv(",,") == [["", "", ""]]


def test_blank_line_is_one_empty_field():
    # "a\n\nb\n" — blank middle record means [""] (one empty field)
    assert parse_csv("a\n\nb\n") == [["a"], [""], ["b"]]


# ---- line endings ---------------------------------------------------------


def test_crlf():
    assert parse_csv("a\r\nb\r\n") == [["a"], ["b"]]


def test_mixed_crlf_and_lf():
    assert parse_csv("a\r\nb\nc\r\n") == [["a"], ["b"], ["c"]]


def test_no_extra_record_for_trailing_newline():
    assert parse_csv("a\n") == [["a"]]


# ---- quoted fields --------------------------------------------------------


def test_quoted_simple():
    assert parse_csv('"a","b"') == [["a", "b"]]


def test_quoted_with_comma():
    assert parse_csv('"a,b","c"') == [["a,b", "c"]]


def test_quoted_with_newline():
    assert parse_csv('"line1\nline2",end') == [["line1\nline2", "end"]]


def test_escaped_quote():
    assert parse_csv('"he said ""hi"""') == [['he said "hi"']]


def test_double_double_quote_alone():
    assert parse_csv('"a""b",c') == [['a"b', "c"]]


def test_empty_quoted_field():
    assert parse_csv('"",a') == [["", "a"]]


def test_quoted_with_crlf_inside():
    assert parse_csv('"a\r\nb",c') == [["a\r\nb", "c"]]


# ---- whitespace handling --------------------------------------------------


def test_unquoted_whitespace_preserved():
    assert parse_csv("  a  ,  b  ") == [["  a  ", "  b  "]]


def test_quoted_whitespace_preserved():
    assert parse_csv('" a "," b "') == [[" a ", " b "]]


def test_whitespace_between_close_quote_and_comma_is_consumed():
    assert parse_csv('"a"  ,"b"  ') == [["a", "b"]]


# ---- BOM handling ---------------------------------------------------------


def test_bom_stripped():
    assert parse_csv("\ufefffirst,second") == [["first", "second"]]


def test_bom_only_at_start_not_mid():
    # BOM in the middle is a literal character (not stripped).
    assert parse_csv("a,\ufeffb") == [["a", "\ufeffb"]]


# ---- errors ---------------------------------------------------------------


def test_unclosed_quote_raises():
    with pytest.raises(ValueError):
        parse_csv('"unterminated')


def test_unclosed_quote_with_content_raises():
    with pytest.raises(ValueError):
        parse_csv('a,"b\nc')


def test_garbage_after_closing_quote_raises():
    # 'x' between closing quote and the comma is invalid per spec §9.
    with pytest.raises(ValueError):
        parse_csv('"a"x,b')


# ---- combined scenarios ---------------------------------------------------


@pytest.mark.parametrize(
    "src, expected",
    [
        ('a,b,c\nd,e,f\n', [["a", "b", "c"], ["d", "e", "f"]]),
        (',\n,\n', [["", ""], ["", ""]]),
        ('"x"\n"y"', [["x"], ["y"]]),
        ('"a,b","c""d","e\nf"', [["a,b", 'c"d', "e\nf"]]),
        ('1,"two",3', [["1", "two", "3"]]),
    ],
)
def test_combined(src, expected):
    assert parse_csv(src) == expected
