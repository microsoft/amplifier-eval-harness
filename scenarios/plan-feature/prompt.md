There's a small Python project at `/workspace`: a CLI greeter that prints a greeting in different languages.

Read the code, then produce a step-by-step **implementation plan** for adding a `--format json` flag.

When `--format json` is set, instead of printing the greeting as plain text, the program should print a JSON object on stdout:

```json
{"name": "Alice", "language": "en", "mood": "warm", "greeting": "Hello there, Alice — lovely to see you."}
```

When `--format json` is omitted (or `--format text` is passed), behavior must be unchanged.

**Critical constraints:**

- Do NOT write code. Do NOT use any tool that modifies files. Read-only is fine.
- Produce a numbered plan, not a prose essay.
- Each step must name the specific file to touch and what changes (function added, signature changed, branch added, etc.). No vague "update the CLI" — say which lines / functions.
- Include any new tests that should be added and where.
- End with a one-line "rollback plan" describing how to revert if the change goes wrong.

Aim for 8–15 numbered steps. Under 500 words total. Plain markdown.
