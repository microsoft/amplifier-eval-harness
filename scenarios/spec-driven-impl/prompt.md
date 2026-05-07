There's a project at `/workspace` with a specification and a skeleton.

Your job: **implement `/workspace/roman.py` per `/workspace/SPEC.md`.**

Workflow:

1. Read `/workspace/SPEC.md` carefully — it defines two functions, their signatures, the validation rules, and a round-trip property.
2. Replace the `NotImplementedError` bodies in `/workspace/roman.py`.
3. Run `pytest /workspace -v` to verify all tests in `/workspace/test_roman.py` pass.

Constraints:

- Do not modify `SPEC.md` or `test_roman.py`.
- Both functions must be implemented (not just one).
- The round-trip property tests must pass.

In your final response: a short summary of the implementation approach for each function, and confirmation that pytest passes with the count.
