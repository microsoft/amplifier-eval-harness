"""Tests for the colors library. DO NOT MODIFY."""

import pytest

from colors import darken, hex_to_rgb, lighten, rgb_to_hex


# ---- hex_to_rgb -----------------------------------------------------------


def test_hex_to_rgb_six_char_with_hash():
    assert hex_to_rgb("#aabbcc") == (170, 187, 204)


def test_hex_to_rgb_six_char_no_hash():
    assert hex_to_rgb("ff0000") == (255, 0, 0)


def test_hex_to_rgb_uppercase():
    assert hex_to_rgb("#AABBCC") == (170, 187, 204)


def test_hex_to_rgb_three_char_shortcut_expanded():
    # "#abc" -> "#aabbcc" -> (170, 187, 204)
    assert hex_to_rgb("#abc") == (170, 187, 204)


def test_hex_to_rgb_three_char_no_hash():
    assert hex_to_rgb("f0a") == (255, 0, 170)


def test_hex_to_rgb_invalid_raises():
    with pytest.raises(ValueError):
        hex_to_rgb("not-a-color")


def test_hex_to_rgb_wrong_length_raises():
    with pytest.raises(ValueError):
        hex_to_rgb("#abcd")


# ---- rgb_to_hex -----------------------------------------------------------


def test_rgb_to_hex_basic():
    # Must use '#' prefix, not '0x'.
    assert rgb_to_hex((170, 187, 204)) == "#aabbcc"


def test_rgb_to_hex_white():
    assert rgb_to_hex((255, 255, 255)) == "#ffffff"


def test_rgb_to_hex_black():
    assert rgb_to_hex((0, 0, 0)) == "#000000"


def test_rgb_to_hex_round_trip():
    assert rgb_to_hex(hex_to_rgb("#3a7bd5")) == "#3a7bd5"


# ---- lighten --------------------------------------------------------------


def test_lighten_zero_is_identity():
    assert lighten((100, 150, 200), 0.0) == (100, 150, 200)


def test_lighten_full_is_white():
    # Channels must clamp at 255 — 100 + 1.0*255 = 355, but max is 255.
    assert lighten((100, 150, 200), 1.0) == (255, 255, 255)


def test_lighten_already_bright_clamps():
    # 200 + 0.5*255 = 327.5 -> int 327; must clamp to 255.
    r, g, b = lighten((200, 200, 200), 0.5)
    assert r == 255 and g == 255 and b == 255


def test_lighten_partial_no_overflow_when_safe():
    # 50 + 0.1*255 = 75.5 -> 75. No clamp needed.
    assert lighten((50, 50, 50), 0.1) == (75, 75, 75)


def test_lighten_invalid_factor_raises():
    with pytest.raises(ValueError):
        lighten((0, 0, 0), -0.1)
    with pytest.raises(ValueError):
        lighten((0, 0, 0), 1.5)


# ---- darken ---------------------------------------------------------------


def test_darken_zero_is_identity():
    assert darken((100, 150, 200), 0.0) == (100, 150, 200)


def test_darken_full_is_black():
    # Channels must clamp at 0 — 100 - 1.0*255 = -155, but min is 0.
    assert darken((100, 150, 200), 1.0) == (0, 0, 0)


def test_darken_already_dark_clamps():
    # 50 - 0.5*255 = -77.5; must clamp to 0.
    r, g, b = darken((50, 50, 50), 0.5)
    assert r == 0 and g == 0 and b == 0


def test_darken_partial_no_underflow_when_safe():
    # 200 - 0.1*255 = 174.5 -> 174.
    assert darken((200, 200, 200), 0.1) == (174, 174, 174)


def test_darken_invalid_factor_raises():
    with pytest.raises(ValueError):
        darken((0, 0, 0), -0.1)
    with pytest.raises(ValueError):
        darken((0, 0, 0), 2.0)
