"""Parse duration strings like '1h30m' into total seconds."""

from __future__ import annotations

import re

# Pattern: hours, optional minutes, optional seconds.
_DURATION_RE = re.compile(r"^(\d+)h(?:(\d+)m)?(?:(\d+)s)?$")


def parse_duration(s: str) -> int:
    """Parse a duration string into total seconds.

    Accepted forms:
        "1h"        -> 3600
        "30m"       -> 1800
        "45s"       -> 45
        "1h30m"     -> 5400
        "2h15m30s"  -> 8130

    Raises:
        ValueError: if the string isn't a recognized duration.
    """
    match = _DURATION_RE.match(s)
    if not match:
        raise ValueError(f"invalid duration: {s!r}")
    h, mi, se = match.groups()
    return int(h) * 3600 + int(mi or 0) * 60 + int(se or 0)
