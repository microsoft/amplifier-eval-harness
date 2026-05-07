There's a Python module at `/workspace/slugify.py` and an empty test stub at `/workspace/test_slugify.py`.

Write a thorough pytest test suite for `slugify()` in `test_slugify.py`. The tests must:

1. Cover the happy path (a normal English string).
2. Cover ASCII normalization of accented characters (e.g. "Café" → "cafe").
3. Cover lowercase normalization.
4. Cover whitespace handling (leading, trailing, runs of internal whitespace).
5. Cover special character collapse (`!`, `@`, `?`, etc. become dashes).
6. Cover the `max_length` truncation, including that truncation falls on a dash boundary when possible.
7. Cover the empty / whitespace-only / all-special-characters error paths (must raise `ValueError`).

Then run `pytest /workspace -v` to confirm all your tests pass against the existing `slugify()` implementation. Do NOT modify `slugify.py`.

In your final response: a short markdown summary listing the test functions you added and pytest's pass count.
