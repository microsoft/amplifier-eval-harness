"""Tiny color-utility library. Several functions are buggy."""

from __future__ import annotations


# Public API: hex_to_rgb, rgb_to_hex, lighten, darken.


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Parse a CSS hex color string into an (r, g, b) tuple of ints in 0..255.

    Accepts:
      - 6-character form: "#aabbcc" or "aabbcc"
      - 3-character shortcut: "#abc" or "abc" (each digit is doubled, so "#abc" -> #aabbcc)
      - Case-insensitive: "#AABBCC" == "#aabbcc"

    Raises ValueError on malformed input.
    """
    s = hex_str.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"invalid hex color: {hex_str!r}")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return (r, g, b)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Format an (r, g, b) tuple as a CSS hex string like '#aabbcc'.

    Output is always 7 characters: a leading '#' followed by 6 lowercase hex digits.
    """
    r, g, b = rgb
    return f"0x{r:02x}{g:02x}{b:02x}"


def lighten(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Lighten an (r, g, b) color by ``factor`` (in [0.0, 1.0]).

    factor=0.0 returns the input unchanged; factor=1.0 returns white (255, 255, 255).
    Each channel moves linearly toward 255 by the given fraction.

    Raises ValueError if factor is outside [0.0, 1.0].
    """
    if factor < 0.0 or factor > 1.0:
        raise ValueError(f"factor must be in [0.0, 1.0], got {factor}")
    r, g, b = rgb
    new_r = int(r + factor * 255)
    new_g = int(g + factor * 255)
    new_b = int(b + factor * 255)
    return (new_r, new_g, new_b)


def darken(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Darken an (r, g, b) color by ``factor`` (in [0.0, 1.0]).

    factor=0.0 returns the input unchanged; factor=1.0 returns black (0, 0, 0).
    Each channel moves linearly toward 0 by the given fraction.

    Raises ValueError if factor is outside [0.0, 1.0].
    """
    if factor < 0.0 or factor > 1.0:
        raise ValueError(f"factor must be in [0.0, 1.0], got {factor}")
    r, g, b = rgb
    new_r = int(r - factor * 255)
    new_g = int(g - factor * 255)
    new_b = int(b - factor * 255)
    return (new_r, new_g, new_b)
