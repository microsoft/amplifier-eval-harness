"""Regression tests: _file_pull_session and collect_usage run even when
_exec_amplifier raises (e.g. subprocess.TimeoutExpired).

Root cause: prior to the fix, if _exec_amplifier() raised the bare
_file_pull_session() call on the next line was skipped, so events.jsonl files
written inside the DTU during a partial run were never retrieved from the
container.

The fix wraps exec+pull in a try/finally so file-pull (step 6) and usage
aggregation (step 6.1) always execute — even on a timeout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from amplifier_eval_harness.config import BundleSpec, RunConfig, RunSpec, ScenarioSpec
from amplifier_eval_harness.gitea import GiteaSession
from amplifier_eval_harness.runner import _run_one_inner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(tmp_path: Path) -> RunSpec:
    """Minimal RunSpec wired to tmp_path; does not touch real filesystem beyond it."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Write hello world")
    return RunSpec(
        bundle=BundleSpec(
            name="test-bundle",
            source="git+https://github.com/microsoft/amplifier-foundation",
        ),
        scenario=ScenarioSpec(
            id="test-scenario",
            prompt_path=prompt_file,
            workspace_path=None,  # skips the workspace-fixture push step
        ),
        run_index=1,
        config=RunConfig(
            config_path=tmp_path / "config.yaml",
            output_dir=tmp_path / "output",
            profile_template=tmp_path / "profile.yaml.tmpl",
            exec_timeout_s=60,
            launch_timeout_s=60,
            keep_dtu_on_failure=False,
            keep_dtu_on_success=False,
        ),
    )


def _make_gitea() -> GiteaSession:
    return GiteaSession(
        instance_id="gitea-test",
        port=10110,
        url="http://localhost:10110",
        token="test-token",
    )


def _patch_infra(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exec_side_effect: BaseException | None = None,
    pull_side_effect: BaseException | None = None,
) -> dict:
    """Patch all DTU-calling functions in runner to no-ops or controlled fakes.

    Returns a mutable state dict the caller can inspect::

        state["pull_calls"]  – list of (instance_id, dest_dir) tuples recorded
                               by the fake _file_pull_session.
    """
    state: dict = {"pull_calls": []}

    rendered = MagicMock()
    rendered.launch_vars = {}
    monkeypatch.setattr("amplifier_eval_harness.runner.render_profile", lambda *a, **kw: rendered)

    def fake_write_profile(spec, rendered, run_dir):  # noqa: ARG001
        p = run_dir / "profile.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# fake profile")
        return p

    monkeypatch.setattr("amplifier_eval_harness.runner.write_profile", fake_write_profile)
    monkeypatch.setattr(
        "amplifier_eval_harness.runner._launch_dtu",
        lambda *a, **kw: ("fake-instance-id", 1.0),
    )
    monkeypatch.setattr("amplifier_eval_harness.runner._wait_readiness", lambda *a, **kw: 0.5)
    monkeypatch.setattr("amplifier_eval_harness.runner._destroy_dtu", lambda *a, **kw: 0.1)
    monkeypatch.setattr(
        "amplifier_eval_harness.runner._verify_workspace",
        lambda *a, **kw: (None, "verify: skipped (mocked)", 0.0),
    )

    def fake_exec(*a, **kw):
        if exec_side_effect is not None:
            raise exec_side_effect
        # Minimal happy-path return: (exit_code, stdout, stderr, elapsed)
        return (0, '{"status":"ok","response":"hello"}', "", 5.0)

    def fake_pull(instance_id, dest_dir):
        state["pull_calls"].append((instance_id, dest_dir))
        if pull_side_effect is not None:
            raise pull_side_effect

    monkeypatch.setattr("amplifier_eval_harness.runner._exec_amplifier", fake_exec)
    monkeypatch.setattr("amplifier_eval_harness.runner._file_pull_session", fake_pull)

    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_file_pull_called_on_exec_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_file_pull_session must be called even when _exec_amplifier raises TimeoutExpired.

    This is the primary regression: events.jsonl files written to the DTU during
    a timed-out run were previously never retrieved from the container, causing
    usage.json to show session_count=0 / request_count=0 for long-running runs.
    """
    exc = subprocess.TimeoutExpired(cmd="amplifier-digital-twin exec", timeout=60)
    state = _patch_infra(monkeypatch, exec_side_effect=exc)

    result = _run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")

    assert len(state["pull_calls"]) == 1, (
        "Expected _file_pull_session to be called once after TimeoutExpired — "
        f"events.jsonl data would have been lost. pull_calls={state['pull_calls']!r}"
    )
    assert state["pull_calls"][0][0] == "fake-instance-id"
    # harness_error semantics must be preserved — the fix only adds data rescue
    assert result.status == "harness_error"
    assert "TimeoutExpired" in (result.error or ""), (
        f"TimeoutExpired should appear in result.error, got: {result.error!r}"
    )


def test_file_pull_called_on_exec_runtime_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_file_pull_session also runs when _exec_amplifier raises a generic RuntimeError."""
    exc = RuntimeError("DTU exec failed (rc=1). Infrastructure failure.")
    state = _patch_infra(monkeypatch, exec_side_effect=exc)

    result = _run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")

    assert len(state["pull_calls"]) == 1, (
        f"_file_pull_session not called after RuntimeError. pull_calls={state['pull_calls']!r}"
    )
    assert result.status == "harness_error"


def test_pull_failure_does_not_mask_exec_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When exec raises AND pull also fails, the original exec error is preserved.

    The DTU may be in a broken state after a timeout; the inner try/except around
    _file_pull_session must swallow the pull error so it doesn't replace the exec
    exception in result.error.
    """
    exec_exc = subprocess.TimeoutExpired(cmd="amplifier-digital-twin", timeout=60)
    pull_exc = RuntimeError("file-pull: broken pipe — DTU container unreachable")
    _patch_infra(monkeypatch, exec_side_effect=exec_exc, pull_side_effect=pull_exc)

    # Must not raise — all errors are captured in result.error
    result = _run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")

    assert result.status == "harness_error"
    assert "TimeoutExpired" in (result.error or ""), (
        f"Pull failure must not mask exec error. result.error={result.error!r}"
    )


def test_file_pull_called_on_exec_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On the happy path, _file_pull_session is still called exactly once.

    Regression guard: the try/finally restructure must not break the normal flow.
    """
    state = _patch_infra(monkeypatch)  # no side_effect → exec succeeds

    result = _run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")

    assert len(state["pull_calls"]) == 1, (
        f"_file_pull_session should be called once on success. pull_calls={state['pull_calls']!r}"
    )
    assert result.status == "success", f"Expected success, got {result.status!r}"
