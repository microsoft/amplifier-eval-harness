"""Text rendering for todo lists."""

from __future__ import annotations

from .models import TodoItem


def render_list(items: list[TodoItem]) -> str:
    """Render a list of TodoItems as human-readable text. Empty list returns "(no items)"."""
    if not items:
        return "(no items)"
    lines: list[str] = []
    for item in items:
        marker = "[x]" if item.status == "done" else "[ ]"
        lines.append(f"{item.id:>3} {marker} {item.text}")
    return "\n".join(lines)
