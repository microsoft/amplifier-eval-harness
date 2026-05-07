There's a small "todoctl" CLI at `/workspace` — a multi-file Python project for managing a todo list.

Your job: **add a `--status` filter to the `list` command.**

Specification:

- The `list` subcommand currently displays all items.
- Add a `--status` option that takes one of: `all` (default), `open`, `done`.
- When `--status open`, only items with status `"open"` are shown.
- When `--status done`, only items with status `"done"` are shown.
- When `--status all` (or omitted), behavior is unchanged.
- The `(no items)` empty-state message should appear if the filter results in zero items.

Constraints:

- All existing tests in `test_todoctl.py` must continue to pass.
- Add at least 2 NEW tests covering `--status open` and `--status done`. Use the same subprocess pattern as the existing tests.
- Run `pytest /workspace -v` to verify everything passes.
- You may modify any file under `/workspace/todoctl/` and `/workspace/test_todoctl.py`.

In your final response: list which files you touched (and why), and confirm pytest's pass count.
