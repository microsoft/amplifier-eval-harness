"""Benchmark harness for dedupe_preserve_order.

Prints ONE line of the form:
    elapsed_ms=<float> n_in=<int> n_out=<int>

Don't modify this file.
"""

from __future__ import annotations

import random
import time

from dedupe import dedupe_preserve_order


def main() -> None:
    rng = random.Random(42)
    # 10k unique tokens, then 50k items drawn from them with replacement.
    pool = [f"item-{i:05d}" for i in range(10_000)]
    items = [rng.choice(pool) for _ in range(50_000)]

    start = time.perf_counter()
    result = dedupe_preserve_order(items)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    print(f"elapsed_ms={elapsed_ms:.1f} n_in={len(items)} n_out={len(result)}")


if __name__ == "__main__":
    main()
