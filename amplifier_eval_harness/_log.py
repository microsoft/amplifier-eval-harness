"""Thread-safe logging helper used by the runner and gitea modules.

When running matrix combos in parallel via ThreadPoolExecutor, each worker
sets a per-thread log prefix so interleaved output remains attributable to
its run_id. Sequential runs work identically with an empty prefix.
"""

from __future__ import annotations

import sys
import threading

_local = threading.local()


def set_prefix(prefix: str) -> None:
    """Set the log prefix for the current thread."""
    _local.prefix = prefix


def clear_prefix() -> None:
    """Remove the log prefix from the current thread."""
    _local.prefix = ""


def get_prefix() -> str:
    """Return the current thread's log prefix (empty if unset)."""
    return getattr(_local, "prefix", "")


def log(msg: str) -> None:
    """Emit a single line to stderr with the current thread's prefix attached."""
    prefix = get_prefix()
    if prefix:
        print(f"[{prefix}] {msg}", file=sys.stderr, flush=True)
    else:
        print(msg, file=sys.stderr, flush=True)
