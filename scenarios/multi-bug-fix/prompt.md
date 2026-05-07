There's a project at `/workspace` containing `colors.py` — a small color-utility library with **multiple distinct bugs**.

Your job: **find and fix all of them.** Make every test in `test_colors.py` pass without modifying the tests.

Workflow:

1. Read `/workspace/colors.py` carefully. There are 4 functions, and each one has at least one bug. Some bugs are subtle.
2. Run `pytest /workspace -v` to see which tests fail. Pay attention to ALL failures — there's more than one bug.
3. Fix every bug.
4. Re-run `pytest` until all tests pass.

Constraints:

- Do not modify `test_colors.py` or any test file.
- Do not relax tests by changing what they assert.
- Each bug should get the smallest fix that makes the relevant test pass without breaking others.

In your final response: list every bug you found (function name + one-sentence description), the fix you applied to each, and the final pytest pass count.
