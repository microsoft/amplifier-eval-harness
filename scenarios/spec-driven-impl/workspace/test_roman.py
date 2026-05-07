"""Tests for roman.py. DO NOT MODIFY."""

import pytest

from roman import int_to_roman, roman_to_int


# ---- roman_to_int: simple letters ------------------------------------------


@pytest.mark.parametrize(
    "s, expected",
    [
        ("I", 1),
        ("V", 5),
        ("X", 10),
        ("L", 50),
        ("C", 100),
        ("D", 500),
        ("M", 1000),
    ],
)
def test_simple_letters(s, expected):
    assert roman_to_int(s) == expected


# ---- roman_to_int: combinations --------------------------------------------


@pytest.mark.parametrize(
    "s, expected",
    [
        ("II", 2),
        ("III", 3),
        ("VI", 6),
        ("VII", 7),
        ("VIII", 8),
        ("XI", 11),
        ("XX", 20),
        ("XXX", 30),
        ("LX", 60),
        ("DCCCLXXXVIII", 888),
        ("MMXXVI", 2026),
    ],
)
def test_combinations(s, expected):
    assert roman_to_int(s) == expected


# ---- roman_to_int: subtractive ---------------------------------------------


@pytest.mark.parametrize(
    "s, expected",
    [
        ("IV", 4),
        ("IX", 9),
        ("XL", 40),
        ("XC", 90),
        ("CD", 400),
        ("CM", 900),
        ("XLIV", 44),
        ("XCIX", 99),
        ("CDXLIV", 444),
        ("CMXCIX", 999),
        ("MCMXCIV", 1994),
        ("MMMCMXCIX", 3999),
    ],
)
def test_subtractive(s, expected):
    assert roman_to_int(s) == expected


# ---- roman_to_int: invalid input -------------------------------------------


@pytest.mark.parametrize(
    "s",
    [
        "",         # empty
        "iv",       # lowercase
        "abc",      # not roman
        "X1",       # mixed
        "IIII",     # too many I
        "XXXX",     # too many X
        "VV",       # V repeated
        "LL",       # L repeated
        "DD",       # D repeated
        "IL",       # invalid subtractive pair
        "IC",       # invalid subtractive pair
        "VX",       # invalid subtractive pair
        "LC",       # invalid subtractive pair
    ],
)
def test_invalid_raises(s):
    with pytest.raises(ValueError):
        roman_to_int(s)


# ---- int_to_roman ---------------------------------------------------------


@pytest.mark.parametrize(
    "n, expected",
    [
        (1, "I"),
        (2, "II"),
        (3, "III"),
        (4, "IV"),
        (5, "V"),
        (9, "IX"),
        (10, "X"),
        (40, "XL"),
        (44, "XLIV"),
        (90, "XC"),
        (99, "XCIX"),
        (400, "CD"),
        (444, "CDXLIV"),
        (900, "CM"),
        (999, "CMXCIX"),
        (1994, "MCMXCIV"),
        (3999, "MMMCMXCIX"),
    ],
)
def test_int_to_roman_examples(n, expected):
    assert int_to_roman(n) == expected


@pytest.mark.parametrize("n", [-1, 0, 4000, 5000])
def test_int_to_roman_out_of_range(n):
    with pytest.raises(ValueError):
        int_to_roman(n)


# ---- round trip ------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 3, 4, 9, 40, 49, 99, 400, 444, 999, 1994, 2026, 3999])
def test_round_trip(n):
    assert roman_to_int(int_to_roman(n)) == n
