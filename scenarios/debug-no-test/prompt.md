There's a Python project at `/workspace` with a user-reported bug. There is **no failing test yet** — that's part of your job.

Workflow:

1. Read `/workspace/BUG_REPORT.md` for the user's symptom report.
2. Read `/workspace/cache.py`. Find the bug.
3. Open `/workspace/test_cache.py` (currently empty) and write a test that **reproduces the bug** — i.e. a test that fails against the current implementation and demonstrates the symptom from the report.
4. Run `pytest /workspace -v` to confirm your test fails.
5. Fix the bug in `/workspace/cache.py`.
6. Run `pytest /workspace -v` again to confirm your test now passes (and that nothing else broke).

Constraints:

- Do not modify `BUG_REPORT.md`.
- Your reproducer test must directly exercise the bug described — not a generic test that incidentally hits it.
- The fix must not relax the docstring's behavior contract.

In your final response: quote the test you wrote, describe the bug in one sentence, and confirm pytest's final pass count.
