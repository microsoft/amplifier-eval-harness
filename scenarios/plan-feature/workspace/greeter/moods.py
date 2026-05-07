"""Time-of-day → mood inference."""

from __future__ import annotations

from datetime import datetime


def infer_mood(now: datetime | None = None) -> str:
    """Infer a default mood from local time-of-day."""
    h = (now or datetime.now()).hour
    if 5 <= h < 11:
        return "warm"
    if 11 <= h < 17:
        return "casual"
    if 17 <= h < 22:
        return "formal"
    return "warm"
