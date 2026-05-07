"""Regression tests for the log pipeline. DO NOT MODIFY.

These tests describe the public contract of process_log_line and must continue
to pass after the refactor.
"""

import pytest

from pipeline import process_log_line


def test_basic_info():
    out = process_log_line("2026-01-01T00:00:00 INFO hello world")
    assert "INFO" in out
    assert "hello world" in out
    assert out.startswith("[2026-01-01T00:00:00]")


def test_warn_level_padded():
    out = process_log_line("2026-01-01T00:00:00 WARN something")
    # "WARN " is padded to 5 chars per the format string.
    assert "WARN " in out


def test_debug_level():
    out = process_log_line("2026-06-15T12:34:56 DEBUG diag")
    assert "DEBUG" in out
    assert "diag" in out


def test_empty_line_raises():
    with pytest.raises(ValueError, match="empty line"):
        process_log_line("")


def test_whitespace_only_raises():
    with pytest.raises(ValueError, match="empty line"):
        process_log_line("   \t  \n")


def test_unparseable_raises():
    with pytest.raises(ValueError, match="unparseable"):
        process_log_line("just one token")


def test_invalid_level_raises():
    with pytest.raises(ValueError, match="invalid level"):
        process_log_line("2026-01-01T00:00:00 NOTREAL hello")


def test_invalid_timestamp_raises():
    with pytest.raises(ValueError, match="invalid timestamp"):
        process_log_line("not-a-timestamp INFO hi")
