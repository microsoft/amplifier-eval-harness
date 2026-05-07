There's a project at `/workspace` with a CSV parser specification and a skeleton.

Your job: **implement `/workspace/csv_parser.py` per `/workspace/SPEC.md`.**

This spec is **strict** — RFC 4180 with extensions for whitespace and BOM handling. Many edge cases. Read SPEC.md carefully.

Workflow:

1. Read `/workspace/SPEC.md` — it covers field separators, quoting, embedded quotes, embedded newlines, empty fields, trailing commas, line endings (LF and CRLF), whitespace preservation, BOM, and error cases.
2. Implement `parse_csv(text: str) -> list[list[str]]` in `/workspace/csv_parser.py`.
3. Run `pytest /workspace -v` to verify all tests in `/workspace/test_csv_parser.py` pass.

Constraints:

- Do not modify `SPEC.md` or `test_csv_parser.py`.
- Don't use Python's `csv` module — implement the parser yourself. The point is to follow the spec's quirks exactly.
- Whitespace handling around unquoted fields is preservation by default (read SPEC.md for the rule).
- Embedded `""` in a quoted field is a literal `"`.
- A trailing comma means a trailing empty field.

In your final response: a short summary of how the parser is structured (state machine? regex? split?) and the pytest pass/fail count.
