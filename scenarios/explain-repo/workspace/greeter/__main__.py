"""greeter CLI entry point.

Run with: python -m greeter --name <name> [--language <code>] [--mood <mood>]
"""

from __future__ import annotations

import argparse
import sys

from .moods import infer_mood
from .render import render_greeting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="greeter")
    parser.add_argument("--name", required=True, help="Person to greet.")
    parser.add_argument("--language", default="en", help="ISO 639-1 language code.")
    parser.add_argument(
        "--mood",
        default=None,
        choices=("casual", "formal", "warm"),
        help="Override the auto-inferred mood.",
    )
    args = parser.parse_args(argv)

    mood = args.mood or infer_mood()
    print(render_greeting(name=args.name, language=args.language, mood=mood))
    return 0


if __name__ == "__main__":
    sys.exit(main())
