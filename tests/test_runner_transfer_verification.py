"""Synthetic transfer/pytest controls; no model calls or retained captures."""

import json
import os
import subprocess
from unittest.mock import Mock

import pytest
from test_runner_file_pull import _make_gitea, _make_spec, _patch_infra

from amplifier_eval_harness import runner


def envelope(code, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        [], 0, json.dumps({"id": "fake", "command": "fake", "exit_code": code,
                           "stdout": stdout, "stderr": stderr}), "",
    )


def test_nested_fixture_push_uses_recursive_flag(tmp_path, monkeypatch):
    workspace = tmp_path / "fixture"
    nested = workspace / "package"
    nested.mkdir(parents=True)
    (nested / "data.txt").write_text("synthetic fixture")
    (workspace / "README.txt").write_text("synthetic")
    call = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(runner, "_run_dtu", call)
    runner._push_workspace_fixture("fake", workspace)
    assert call.call_args.args[0] == [
        "file-push", "-r", "fake", str(workspace / "README.txt"), str(nested), "/workspace/",
    ]


def test_empty_fixture_does_not_launch_invalid_transfer(tmp_path, monkeypatch):
    call = Mock()
    monkeypatch.setattr(runner, "_run_dtu", call)
    runner._push_workspace_fixture("fake", tmp_path)
    call.assert_not_called()


def test_session_pull_is_recursive_and_failure_is_not_swallowed(tmp_path, monkeypatch):
    call = Mock(return_value=subprocess.CompletedProcess([], 1, "", "synthetic failure"))
    monkeypatch.setattr(runner, "_run_dtu", call)
    with pytest.raises(RuntimeError, match="SESSION_PULL_FAILED"):
        runner._file_pull_session("fake", tmp_path)
    assert call.call_args.args[0] == [
        "file-pull", "-r", "fake", "/root/.amplifier/projects/", str(tmp_path / "session"),
    ]


@pytest.mark.parametrize("code, expected", [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, None), (125, 125)])
def test_verification_retains_native_pytest_exit(monkeypatch, code, expected):
    call = Mock(return_value=envelope(code, "synthetic pytest summary"))
    monkeypatch.setattr(runner, "_run_dtu", call)
    rc, summary, elapsed = runner._verify_workspace("fake")
    assert rc == expected and elapsed >= 0
    assert "synthetic pytest summary" in summary
    args = call.call_args.args[0]
    assert args[:7] == ["exec", "--timeout", "180", "fake", "--", "bash", "-c"]
    assert "${PIPESTATUS[0]}" in args[-1]


@pytest.mark.parametrize("payload", [
    "not JSON", "[]", "null", '{"exit_code":false,"stdout":"","stderr":""}',
    '{"exit_code":"0","stdout":"","stderr":""}', '{"stdout":"","stderr":""}',
    '{"exit_code":0,"stdout":{},"stderr":""}',
])
def test_invalid_verification_envelope_is_error_not_skip(monkeypatch, payload):
    monkeypatch.setattr(runner, "_run_dtu", lambda *a, **kw: subprocess.CompletedProcess([], 0, payload, ""))
    rc, _, _ = runner._verify_workspace("fake")
    assert rc == -1


def test_verification_transport_failure_is_error_not_skip(monkeypatch):
    monkeypatch.setattr(runner, "_run_dtu", lambda *a, **kw: subprocess.CompletedProcess([], 1, "", ""))
    assert runner._verify_workspace("fake")[0] == -1


@pytest.mark.parametrize("source, expected", [
    ("def test_ok():\n    assert True\n", 0),
    ("def test_bad():\n    assert False\n", 1),
    ("def test_broken(\n", 2),
    (None, None),
])
def test_actual_bash_pipeline_does_not_replace_pytest_status_with_tail(tmp_path, monkeypatch, source, expected):
    """Run benign synthetic pytest locally using the actual generated bash command."""
    if source is not None:
        (tmp_path / "test_synthetic.py").write_text(source)

    def run(args, **kw):
        assert args[-3:-1] == ["bash", "-c"]
        actual = subprocess.run(
            ["bash", "-c", args[-1]], capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        )
        return envelope(actual.returncode, actual.stdout, actual.stderr)

    monkeypatch.setattr(runner, "_run_dtu", run)
    assert runner._verify_workspace("fake", workspace_path=str(tmp_path))[0] == expected


@pytest.mark.parametrize("code, status, signal", [
    (0, "success", "passed"), (1, "task_failed", "failed"),
    (2, "verification_error", "error"), (-1, "verification_error", "error"),
    (None, "success", "skipped"),
])
def test_run_one_keeps_verification_distinct_from_cli_return(tmp_path, monkeypatch, code, status, signal):
    _patch_infra(monkeypatch)
    monkeypatch.setattr(runner, "_verify_workspace", lambda *a: (code, "synthetic", 0.0))
    result = runner._run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")
    assert result.exit_code == 0 and result.error is None
    assert result.status == status
    assert result.extras["verification"] == signal


def test_verification_exception_cannot_be_reported_as_success(tmp_path, monkeypatch):
    _patch_infra(monkeypatch)
    monkeypatch.setattr(runner, "_verify_workspace", Mock(side_effect=subprocess.TimeoutExpired("fake", 1)))
    result = runner._run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")
    assert result.exit_code == 0 and result.verify_exit_code == -1
    assert result.status == "verification_error" and result.extras["verification"] == "error"


def test_session_transfer_failure_cannot_be_reported_as_success(tmp_path, monkeypatch):
    _patch_infra(monkeypatch, pull_side_effect=RuntimeError("synthetic"))
    result = runner._run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")
    assert result.exit_code == 0
    assert result.status == "collection_error" and result.extras["session_pull"] == "failed"


@pytest.mark.parametrize("code", [1, 2, -1])
def test_combined_collection_and_verification_failure_keeps_both_signals(tmp_path, monkeypatch, code):
    _patch_infra(monkeypatch, pull_side_effect=RuntimeError("synthetic"))
    monkeypatch.setattr(runner, "_verify_workspace", lambda *a: (code, "synthetic", 0.0))
    result = runner._run_one_inner(_make_spec(tmp_path), _make_gitea(), tmp_path / "run")
    assert result.exit_code == 0 and result.verify_exit_code == code
    assert result.status == "collection_error" and result.extras["session_pull"] == "failed"
    assert result.extras["verification"] == ("failed" if code == 1 else "error")


def test_manifest_error_counts_are_independent(tmp_path, monkeypatch):
    import datetime

    from amplifier_eval_harness.results import write_manifest

    _patch_infra(monkeypatch, pull_side_effect=RuntimeError("synthetic"))
    monkeypatch.setattr(runner, "_verify_workspace", lambda *a: (2, "synthetic", 0.0))
    spec = _make_spec(tmp_path)
    result = runner._run_one_inner(spec, _make_gitea(), tmp_path / "run")
    instant = datetime.datetime(2026, 1, 1)
    write_manifest(tmp_path, spec.config, _make_gitea(), instant, instant, [result])
    report = json.loads((tmp_path / "manifest.json").read_text())
    assert report["collection_errors"] == report["verification_errors"] == 1
    assert report["successes"] == report["task_failures"] == 0


@pytest.mark.parametrize("code, expected", [(0, 0), (1, 1), (2, 2), (5, None)])
def test_path_installed_pytest_does_not_require_system_python_module(tmp_path, monkeypatch, code, expected):
    tools = tmp_path / "tools"
    tools.mkdir()
    pytest_tool = tools / "pytest"
    pytest_tool.write_text(f"#!/bin/sh\nexit {code}\n")
    pytest_tool.chmod(0o700)
    (tools / "tail").symlink_to("/usr/bin/tail")
    python_tool = tools / "python3"
    python_tool.write_text("#!/bin/sh\nexit 1\n")
    python_tool.chmod(0o700)

    def run(args, **kw):
        actual = subprocess.run(
            ["/bin/bash", "-c", args[-1]], capture_output=True, text=True, timeout=10,
            env={"PATH": str(tools)},
        )
        return envelope(actual.returncode, actual.stdout, actual.stderr)

    monkeypatch.setattr(runner, "_run_dtu", run)
    assert runner._verify_workspace("fake", workspace_path=str(tmp_path))[0] == expected


def test_missing_pytest_executable_and_module_is_infrastructure_error(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "tail").symlink_to("/usr/bin/tail")
    python_tool = tools / "python3"
    python_tool.write_text("#!/bin/sh\nexit 1\n")
    python_tool.chmod(0o700)

    def run(args, **kw):
        actual = subprocess.run(
            ["/bin/bash", "-c", args[-1]], capture_output=True, text=True, timeout=10,
            env={"PATH": str(tools)},
        )
        return envelope(actual.returncode, actual.stdout, actual.stderr)

    monkeypatch.setattr(runner, "_run_dtu", run)
    assert runner._verify_workspace("fake", workspace_path=str(tmp_path))[0] == 126


def test_verification_error_honors_keep_dtu_on_failure(tmp_path, monkeypatch):
    _patch_infra(monkeypatch)
    monkeypatch.setattr(runner, "_verify_workspace", lambda *a: (2, "synthetic", 0.0))
    destroy = Mock()
    monkeypatch.setattr(runner, "_destroy_dtu", destroy)
    spec = _make_spec(tmp_path)
    spec.config.keep_dtu_on_failure = True
    result = runner._run_one_inner(spec, _make_gitea(), tmp_path / "run")
    assert result.status == "verification_error"
    destroy.assert_not_called()
