There's a project at `/workspace` with a slow function and a benchmark.

Your job: **make `dedupe_preserve_order` substantially faster, but DO NOT change its semantics.**

Workflow:

1. Read `/workspace/dedupe.py` and `/workspace/test_dedupe.py`.
2. Run `python /workspace/bench.py` to measure the current performance. Capture `elapsed_ms`.
3. Optimize `dedupe_preserve_order` for speed.
4. Run `python /workspace/bench.py` again to confirm speedup.
5. Run `pytest /workspace -v` to confirm correctness wasn't lost.

Constraints:

- Function signature, docstring contract, and behavior on documented inputs must be unchanged.
- The function's name says "preserve_order" — and the tests check that. Read carefully before optimizing.
- Don't modify `bench.py` or `test_dedupe.py`.
- The improvement must be measurable: at least a 5× speedup.

In your final response: report before/after times verbatim from `bench.py`, explain the fix in one sentence, and confirm pytest passes.
