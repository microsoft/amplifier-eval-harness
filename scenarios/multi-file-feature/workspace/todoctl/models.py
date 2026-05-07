"""Data model for todo items."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Status = Literal["open", "done"]


@dataclass
class TodoItem:
    """A single todo entry."""

    id: int
    text: str
    status: Status = "open"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "status": self.status, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> TodoItem:
        return cls(id=d["id"], text=d["text"], status=d["status"], created_at=d["created_at"])
