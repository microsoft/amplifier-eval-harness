"""URL-safe slug generation."""

from __future__ import annotations

import re
import unicodedata


def slugify(text: str, max_length: int = 60) -> str:
    """Convert arbitrary text into a URL-safe slug.

    Steps applied, in order:
      1. ASCII-fold (strip accents/diacritics via NFKD normalization).
      2. Lowercase.
      3. Replace any run of non-alphanumeric characters with a single dash.
      4. Strip leading/trailing dashes.
      5. Truncate to ``max_length`` characters; if truncation lands inside
         a non-dash run, walk back to the nearest dash so we don't split a
         word in the middle.

    Raises:
        ValueError: if the input is empty / whitespace-only, or contains
                    only characters that get stripped (e.g. "!!!" or "+++").
    """
    if not text or not text.strip():
        raise ValueError("empty text")

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")

    if not text:
        raise ValueError("text contains only non-slug characters")

    if len(text) > max_length:
        text = text[:max_length]
        # If we landed inside a word, walk back to the nearest dash.
        if "-" in text and not text.endswith("-"):
            last_dash = text.rfind("-")
            if last_dash > 0:
                text = text[:last_dash]
        text = text.rstrip("-")

    return text
