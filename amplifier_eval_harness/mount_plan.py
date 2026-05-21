"""Post-run extraction of the resolved mount plan from session events.jsonl.

When `session.raw: true` is set (forced by the harness's force-raw overlay),
amplifier-core emits the resolved mount plan in the session events stream.

**Current schema (kernel ≥ May 21 2026):**
The mount plan is in the first ``session:config`` event whose ``data.raw``
is a dict.  This event was introduced when the kernel separated session
initialisation from session configuration.

**Legacy schema (kernel < May 21 2026):**
The mount plan was in the *second* ``session:start`` event whose ``data.raw``
is a dict.  A fallback path preserves the ability to re-process old
eval-results directories (e.g. runs captured before the schema change).

We extract the mount plan and write it as <run_dir>/mount_plan.json.

The mount plan is the ground truth for "what was actually composed for this
evaluation": providers, tools, hooks, agents, orchestrator, context — after
amplifier-app-cli's overlays and force-composition have been applied. A
hash of the canonicalized plan goes into summary.csv as `mount_plan_sha`
so drift between runs is detectable at a glance.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _first_session_start(events_path: Path) -> dict[str, Any] | None:
    """Return the FIRST session:start event in this file, or None."""
    try:
        with events_path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "session:start":
                    return ev
        return None
    except OSError:
        return None


def _find_raw_mount_plan(events_path: Path) -> dict[str, Any] | None:
    """Find the resolved mount plan in events.jsonl.

    Primary location: the first ``session:config`` event whose ``data.raw`` is
    a dict.  The kernel emits this event when ``session.raw: true`` is set
    (kernel schema since May 21 2026).

    Fallback (legacy schema, pre-May 21 2026): the second ``session:start``
    event whose ``data.raw`` is a dict.  Preserved so old eval-results dirs
    can still be re-processed.

    Returns the raw mount-plan dict, or None if no candidate was found.
    """
    try:
        with events_path.open("r") as f:
            session_start_with_raw: dict[str, Any] | None = None
            session_start_count = 0
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = ev.get("event")
                data = ev.get("data") or {}
                raw = data.get("raw") if isinstance(data, dict) else None
                if event == "session:config" and isinstance(raw, dict):
                    return raw  # primary — preferred, early return
                if event == "session:start":
                    session_start_count += 1
                    if session_start_count >= 2 and isinstance(raw, dict):
                        session_start_with_raw = raw
            return session_start_with_raw  # legacy fallback, or None
    except OSError:
        return None


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def extract_mount_plan(session_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Walk a pulled session directory and return (mount_plan, sha256) for the root session.

    `session_root` is the directory that contains the pulled
    `/root/.amplifier/projects/` tree. We find events.jsonl files, pick the one
    whose first session:start event has parent_id == None (i.e. the root
    session of this run), then locate its second session:start event whose
    data.raw carries the resolved mount plan.

    Returns (None, None) if no root session with a raw payload is found.
    """
    if not session_root.is_dir():
        return None, None

    candidates = sorted(session_root.rglob("events.jsonl"))
    for events_path in candidates:
        first = _first_session_start(events_path)
        if first is None:
            continue
        data = first.get("data") or {}
        if data.get("parent_id") is not None:
            continue  # not a root session
        mount_plan = _find_raw_mount_plan(events_path)
        if mount_plan is not None:
            return mount_plan, _canonical_sha256(mount_plan)
    return None, None
