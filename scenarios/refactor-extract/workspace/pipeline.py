"""Log line processing pipeline.

Currently a single function that does parsing + validation + formatting.
Should be refactored into three single-purpose helpers.
"""

from __future__ import annotations

import re
from datetime import datetime

_LOG_RE = re.compile(r"^(?P<ts>\S+)\s+(?P<level>\w+)\s+(?P<msg>.*)$")
_VALID_LEVELS = {"INFO", "WARN", "ERROR", "DEBUG"}


def process_log_line(line: str) -> str:
    """Parse a log line, validate it, and return a normalized formatted string.

    Expected input shape: "<iso8601-timestamp> <LEVEL> <free-form message>"

    Returns a string of the form:
        "[<iso8601-timestamp>] <LEVEL>  <message>"

    Raises:
        ValueError: if the line is empty, unparseable, has an unknown level,
                    or has an invalid timestamp.
    """
    line = line.strip()
    if not line:
        raise ValueError("empty line")

    match = _LOG_RE.match(line)
    if not match:
        raise ValueError(f"unparseable: {line!r}")

    ts_raw = match.group("ts")
    level = match.group("level")
    msg = match.group("msg")

    if level not in _VALID_LEVELS:
        raise ValueError(f"invalid level: {level!r}")

    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError as e:
        raise ValueError(f"invalid timestamp: {ts_raw!r}") from e

    return f"[{ts.isoformat()}] {level:<5} {msg}"
