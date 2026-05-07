"""Tests for parse_duration. DO NOT MODIFY."""

import pytest

from durations import parse_duration


def test_hours_only():
    assert parse_duration("1h") == 3600


def test_two_hours():
    assert parse_duration("2h") == 7200


def test_minutes_only():
    assert parse_duration("30m") == 1800


def test_seconds_only():
    assert parse_duration("45s") == 45


def test_hours_and_minutes():
    assert parse_duration("1h30m") == 5400


def test_full_combination():
    assert parse_duration("2h15m30s") == 2 * 3600 + 15 * 60 + 30


def test_invalid_string_raises():
    with pytest.raises(ValueError):
        parse_duration("not-a-duration")


def test_empty_string_raises():
    with pytest.raises(ValueError):
        parse_duration("")
