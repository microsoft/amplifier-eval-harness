"""Tiny benchmark harness for count_unique_words.

Prints ONE line of the form:
    elapsed_ms=<float> unique=<int> total_words=<int>

Don't modify this file — the prompt tells you to capture the elapsed_ms
verbatim from this output before and after your fix.
"""

from __future__ import annotations

import time

from text_proc import count_unique_words


def main() -> None:
    # Build a corpus with mostly-repeating words and a long tail of unique
    # tokens, so the slow O(n^2) scan is forced to chew through the whole
    # 'seen' list on most of the appends.
    base = "the quick brown fox jumps over the lazy dog "
    corpus = (base * 1500) + " ".join(f"unique{i:05d}" for i in range(2000))

    total_words = len(corpus.split())

    start = time.perf_counter()
    n = count_unique_words(corpus)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    print(f"elapsed_ms={elapsed_ms:.1f} unique={n} total_words={total_words}")


if __name__ == "__main__":
    main()
