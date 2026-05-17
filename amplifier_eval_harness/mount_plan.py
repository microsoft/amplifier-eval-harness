"""Post-run extraction of the resolved mount plan from session events.jsonl.

When `session.raw: true` is set (forced by the harness's force-raw overlay),
amplifier-core emits a second `session:start` event whose `data.raw` field
contains the redacted, post-overlay, post-force-composition mount plan.
We extract that here and write it as <run_dir>/mount_plan.json.

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
    """Find the SECOND session:start event (the one with data.raw)."""
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
                if ev.get("event") != "session:start":
                    continue
                data = ev.get("data") or {}
                raw = data.get("raw")
                if isinstance(raw, dict):
                    return raw
        return None
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
