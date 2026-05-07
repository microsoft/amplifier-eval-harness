"""Time-of-day → mood inference."""

from __future__ import annotations

from datetime import datetime


def infer_mood(now: datetime | None = None) -> str:
    """Infer a default mood from local time-of-day.

    Bands:
      - 05:00–11:00 → 'warm'   (morning)
      - 11:00–17:00 → 'casual' (afternoon)
      - 17:00–22:00 → 'formal' (evening)
      - 22:00–05:00 → 'warm'   (late night, soft)
    """
    h = (now or datetime.now()).hour
    if 5 <= h < 11:
        return "warm"
    if 11 <= h < 17:
        return "casual"
    if 17 <= h < 22:
        return "formal"
    return "warm"
