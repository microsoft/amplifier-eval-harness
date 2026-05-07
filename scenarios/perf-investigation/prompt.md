There's a Python project at `/workspace` with a slow function and a benchmarking script.

Your job: **find the bottleneck, fix it, prove it.**

Workflow:

1. Run `python /workspace/bench.py` to measure the current performance. Capture the elapsed time.
2. Read `/workspace/text_proc.py`. Identify the algorithmic bottleneck (not just micro-tweaks — there's a clear complexity issue).
3. Fix `text_proc.py` so it produces the SAME result for the SAME input but runs substantially faster.
4. Run `python /workspace/bench.py` again to confirm.
5. Run `pytest /workspace -v` to confirm correctness wasn't lost (the existing tests describe the function's contract).

Constraints:

- The function's signature, return type, and behavior on documented inputs must be unchanged.
- Don't modify `bench.py` or `test_text_proc.py`.
- The improvement must be measurable: at least a 5× speedup on the benchmark.

In your final response: report the before and after times in milliseconds (verbatim from `bench.py` output), explain the fix in one sentence, and confirm `pytest` still passes.
