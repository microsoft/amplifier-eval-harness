There's a Python project at `/workspace` with a function that does too much:
`process_log_line` in `pipeline.py` parses, validates, and formats in one body.

Refactor it into three single-purpose functions while preserving behavior:

1. `parse(line: str) -> dict` — splits the line into fields. Returns a dict with keys `ts_raw`, `level`, `msg`. Raises `ValueError` only on truly unparseable input.
2. `validate(record: dict) -> dict` — checks the level and parses the timestamp. Raises `ValueError` on invalid level or invalid timestamp. Returns a dict with `ts` (datetime), `level`, `msg`.
3. `format_record(record: dict) -> str` — returns the final formatted string in the same shape `process_log_line` returns today.

Then rewrite `process_log_line` to call the three helpers in sequence. The public API of `process_log_line` (signature, raised exceptions, return string) must not change.

Verify:

- `pytest /workspace -v` passes (the existing tests in `test_pipeline.py` are the contract — do not modify them).
- All three new helpers are top-level functions in `pipeline.py`.

In your final response: a short markdown summary listing the three new helpers and confirming tests pass.
