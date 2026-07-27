"""Unit tests for amplifier_eval_harness.mount_plan._find_raw_mount_plan.

Covers the three extraction scenarios:
  1. New schema (kernel ≥ May 21 2026): session:config event with data.raw
  2. Legacy schema (kernel < May 21 2026): second session:start with data.raw
  3. Neither present: returns None
"""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_eval_harness.mount_plan import _canonical_sha256, _find_raw_mount_plan, extract_mount_plan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_MOUNT_PLAN: dict = {
    "agents": ["agent-explorer"],
    "application_host": "localhost",
    "bundle_name": "test-bundle",
    "hooks": ["hooks-logging"],
    "project_dir": "/workspace",
    "project_name": "test-project",
    "project_slug": "test-project",
    "providers": ["provider-openai"],
    "root_session_id": "abc123",
    "session": {"orchestrator": "loop-streaming", "context": "context-simple"},
    "tools": ["tool-todo"],
    "working_dir": "/workspace",
}


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    """Write a list of event dicts to an events.jsonl file and return the path."""
    p = tmp_path / "events.jsonl"
    with p.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return p


# ---------------------------------------------------------------------------
# Test 1: New schema — session:config event carries data.raw
# ---------------------------------------------------------------------------


def test_find_raw_mount_plan_uses_session_config(tmp_path: Path) -> None:
    """Primary path: session:config event with data.raw returns the mount plan."""
    events = [
        {"event": "session:start", "data": {"parent_id": None}},
        {"event": "session:config", "data": {"parent_id": None, "raw": _SAMPLE_MOUNT_PLAN}},
        {"event": "tool:call", "data": {"name": "tool-todo"}},
    ]
    events_path = _write_events(tmp_path, events)

    result = _find_raw_mount_plan(events_path)

    assert result is not None, "Expected mount plan from session:config, got None"
    assert result["bundle_name"] == "test-bundle"
    assert result["providers"] == ["provider-openai"]
    assert result["tools"] == ["tool-todo"]


# ---------------------------------------------------------------------------
# Test 2: Legacy schema — second session:start carries data.raw
# ---------------------------------------------------------------------------


def test_find_raw_mount_plan_falls_back_to_second_session_start(tmp_path: Path) -> None:
    """Fallback path: second session:start with data.raw (old kernel schema) is extracted."""
    events = [
        {"event": "session:start", "data": {"parent_id": None}},  # first — no raw
        {"event": "session:start", "data": {"parent_id": None, "raw": _SAMPLE_MOUNT_PLAN}},  # second — has raw
        {"event": "tool:call", "data": {"name": "tool-todo"}},
    ]
    events_path = _write_events(tmp_path, events)

    result = _find_raw_mount_plan(events_path)

    assert result is not None, "Expected mount plan from second session:start fallback, got None"
    assert result["bundle_name"] == "test-bundle"
    assert result["agents"] == ["agent-explorer"]


# ---------------------------------------------------------------------------
# Test 3: Neither present — returns None
# ---------------------------------------------------------------------------


def test_find_raw_mount_plan_returns_none_when_neither_present(tmp_path: Path) -> None:
    """No session:config and only a single session:start (no raw) → None."""
    events = [
        {"event": "session:start", "data": {"parent_id": None}},  # first only, no raw
        {"event": "tool:call", "data": {"name": "tool-todo"}},
        {"event": "tool:result", "data": {"result": "ok"}},
    ]
    events_path = _write_events(tmp_path, events)

    result = _find_raw_mount_plan(events_path)

    assert result is None


# ---------------------------------------------------------------------------
# Test 4: session:config preferred over second session:start if both present
# ---------------------------------------------------------------------------


def test_find_raw_mount_plan_prefers_session_config_over_second_session_start(tmp_path: Path) -> None:
    """When both new and legacy signals exist, session:config wins."""
    legacy_plan = {**_SAMPLE_MOUNT_PLAN, "bundle_name": "legacy-bundle"}
    new_plan = {**_SAMPLE_MOUNT_PLAN, "bundle_name": "new-bundle"}

    events = [
        {"event": "session:start", "data": {"parent_id": None}},
        # Legacy second session:start appears BEFORE session:config
        {"event": "session:start", "data": {"parent_id": None, "raw": legacy_plan}},
        {"event": "session:config", "data": {"parent_id": None, "raw": new_plan}},
    ]
    events_path = _write_events(tmp_path, events)

    result = _find_raw_mount_plan(events_path)

    assert result is not None
    assert result["bundle_name"] == "new-bundle", (
        "session:config should be preferred even when second session:start appears earlier"
    )


# ---------------------------------------------------------------------------
# Test 5: extract_mount_plan integration — new schema via session root dir
# ---------------------------------------------------------------------------


def test_extract_mount_plan_new_schema_end_to_end(tmp_path: Path) -> None:
    """extract_mount_plan() returns (plan, sha) for a root session using new schema."""
    session_dir = tmp_path / "root-session-id"
    session_dir.mkdir()

    events = [
        {"event": "session:start", "data": {"parent_id": None}},
        {"event": "session:config", "data": {"parent_id": None, "raw": _SAMPLE_MOUNT_PLAN}},
    ]
    _write_events(session_dir, events)

    plan, sha = extract_mount_plan(tmp_path)

    assert plan is not None, "extract_mount_plan should return a plan for new schema"
    assert plan["bundle_name"] == "test-bundle"
    assert sha is not None
    assert len(sha) == 64, "SHA-256 hex digest should be 64 characters"


# ---------------------------------------------------------------------------
# Test 6: extract_mount_plan integration — child sessions are skipped
# ---------------------------------------------------------------------------


def test_extract_mount_plan_skips_child_sessions(tmp_path: Path) -> None:
    """extract_mount_plan() skips events.jsonl files whose first session:start has a parent_id."""
    child_dir = tmp_path / "child-session-id"
    child_dir.mkdir()

    # Child session: parent_id is set → should be skipped
    child_events = [
        {"event": "session:start", "data": {"parent_id": "some-parent"}},
        {"event": "session:config", "data": {"parent_id": "some-parent", "raw": _SAMPLE_MOUNT_PLAN}},
    ]
    _write_events(child_dir, child_events)

    plan, sha = extract_mount_plan(tmp_path)

    assert plan is None, "Child sessions should be skipped; expected None"
    assert sha is None


# ---------------------------------------------------------------------------
# Test 7: hash normalization — root_session_id is per-session ephemera
# ---------------------------------------------------------------------------


def test_hash_ignores_root_session_id() -> None:
    """Two plans differing ONLY in root_session_id must hash-equal.

    root_session_id is a fresh UUID per session; hashing it verbatim would
    guarantee false drift between runs of the identical configuration.
    """
    plan_a = {**_SAMPLE_MOUNT_PLAN, "root_session_id": "uuid-run-one"}
    plan_b = {**_SAMPLE_MOUNT_PLAN, "root_session_id": "uuid-run-two"}

    assert _canonical_sha256(plan_a) == _canonical_sha256(plan_b), (
        "Plans identical except root_session_id must produce the same hash"
    )


def test_hash_detects_real_config_drift() -> None:
    """Plans differing in any real configuration key must hash-differ."""
    baseline_sha = _canonical_sha256(_SAMPLE_MOUNT_PLAN)

    for key, drifted_value in [
        ("bundle_name", "other-bundle"),
        ("providers", ["provider-anthropic"]),
        ("tools", ["tool-todo", "tool-bash"]),
        ("session", {"orchestrator": "loop-basic", "context": "context-simple"}),
    ]:
        drifted = {**_SAMPLE_MOUNT_PLAN, key: drifted_value}
        assert _canonical_sha256(drifted) != baseline_sha, f"Drift in real config key {key!r} must change the hash"


def test_hash_does_not_mutate_plan() -> None:
    """Normalization strips keys for hashing only; the plan dict is untouched."""
    plan = dict(_SAMPLE_MOUNT_PLAN)

    _canonical_sha256(plan)

    assert plan == _SAMPLE_MOUNT_PLAN
    assert "root_session_id" in plan, "Full plan (session id included) must remain available"


def test_extract_mount_plan_sha_stable_across_sessions(tmp_path: Path) -> None:
    """End-to-end: two runs of the identical config (different session ids) hash-match."""
    shas: list[str] = []
    for run, session_id in [("run-one", "session-uuid-1"), ("run-two", "session-uuid-2")]:
        run_root = tmp_path / run
        session_dir = run_root / session_id
        session_dir.mkdir(parents=True)
        plan = {**_SAMPLE_MOUNT_PLAN, "root_session_id": session_id}
        events = [
            {"event": "session:start", "data": {"parent_id": None}},
            {"event": "session:config", "data": {"parent_id": None, "raw": plan}},
        ]
        _write_events(session_dir, events)

        extracted_plan, sha = extract_mount_plan(run_root)

        assert extracted_plan is not None
        assert extracted_plan["root_session_id"] == session_id, "Extracted plan must keep the session id verbatim"
        assert sha is not None
        shas.append(sha)

    assert shas[0] == shas[1], "Identical configuration across sessions must produce identical mount_plan_sha"
