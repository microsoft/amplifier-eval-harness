"""Text processing utilities.

Currently slow on large inputs. The contract (signature, return semantics)
is fixed; the implementation can be replaced with anything that satisfies it.
"""

from __future__ import annotations


def count_unique_words(text: str) -> int:
    """Return the count of distinct whitespace-separated tokens in ``text``,
    case-insensitively.

    Examples:
        >>> count_unique_words("the quick brown fox")
        4
        >>> count_unique_words("The the THE")
        1
        >>> count_unique_words("")
        0
    """
    words = text.lower().split()
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return len(seen)
