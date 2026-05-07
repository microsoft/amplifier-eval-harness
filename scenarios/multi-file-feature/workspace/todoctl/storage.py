"""JSON-file-backed persistence for TodoItem lists."""

from __future__ import annotations

import json
from pathlib import Path

from .models import TodoItem


def load(path: Path) -> list[TodoItem]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [TodoItem.from_dict(item) for item in raw]


def save(path: Path, items: list[TodoItem]) -> None:
    payload = [item.to_dict() for item in items]
    path.write_text(json.dumps(payload, indent=2))
