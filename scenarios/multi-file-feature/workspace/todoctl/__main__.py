"""todoctl CLI entry point.

Usage:
    python -m todoctl --file <path> add "buy milk"
    python -m todoctl --file <path> list
    python -m todoctl --file <path> done <id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import TodoItem
from .render import render_list
from .storage import load, save


def cmd_add(args: argparse.Namespace) -> int:
    items = load(args.file)
    next_id = max((i.id for i in items), default=0) + 1
    items.append(TodoItem(id=next_id, text=args.text))
    save(args.file, items)
    print(f"Added: #{next_id} {args.text}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    items = load(args.file)
    print(render_list(items))
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    items = load(args.file)
    for item in items:
        if item.id == args.id:
            item.status = "done"
            save(args.file, items)
            print(f"Done: #{args.id}")
            return 0
    print(f"No item with id {args.id}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="todoctl")
    parser.add_argument("--file", type=Path, required=True, help="Path to the JSON store.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add a new todo.")
    p_add.add_argument("text")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="Show all todos.")
    p_list.set_defaults(func=cmd_list)

    p_done = sub.add_parser("done", help="Mark a todo as done.")
    p_done.add_argument("id", type=int)
    p_done.set_defaults(func=cmd_done)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
