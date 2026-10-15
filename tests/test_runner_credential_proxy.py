"""Runner integration controls, with fake credentials and mocked DTU/Docker.

These exercise the ordinary run_one path, not model quality or container escape.
"""

import json
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from amplifier_eval_harness import runner
from amplifier_eval_harness.config import (
    BundleSpec,
    CredentialProxyConfig,
    RunConfig,
    RunSpec,
    ScenarioSpec,
    load_config,
)
from amplifier_eval_harness.gitea import GiteaSession
from amplifier_eval_harness.profile import render_profile

FAKE_KEY = "FAKE_HOST_ONLY_UPSTREAM_KEY_0123456789"
FAKE_TOKEN = "FAKE_SOLVER_ONLY_TEMPORARY_TOKEN"
PROXY_URL = "http://10.0.0.1:12345/v1"
IDENTIFIER = "a" * 64


def spec_for(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("A public fixture prompt")
    config = RunConfig(
        config_path=tmp_path / "config.yaml",
        output_dir=tmp_path / "out",
        profile_template=Path(__file__).parents[1] / "profiles" / "eval-base.yaml.tmpl",
        keep_dtu_on_failure=False,
        credential_proxy=CredentialProxyConfig("10.0.0.1", "TEST_VENDOR_KEY", "TEST_VENDOR_BASE"),
    )
    return RunSpec(
        BundleSpec("test-bundle", "git+https://github.com/microsoft/amplifier-foundation"),
        ScenarioSpec("fixture", prompt), 1, config,
    )


@pytest.fixture
def fake_host(monkeypatch):
    monkeypatch.setenv("TEST_VENDOR_KEY", FAKE_KEY)
    monkeypatch.setenv("TEST_VENDOR_BASE", "https://api.example.test/v1")
    monkeypatch.setenv("OTHER_VENDOR_SECRET", "FAKE_OTHER_SECRET")


def mock_infra(monkeypatch, *, exec_error=None, cleanup_error=False):
    order = []
    launch = Mock(return_value=("fake-dtu", 1.0))
    monkeypatch.setattr(runner, "_launch_dtu", launch)
    monkeypatch.setattr(runner, "_wait_readiness", lambda *a: 0.1)
    monkeypatch.setattr(runner, "_destroy_dtu", lambda *a: order.append("destroy") or 0.1)
    monkeypatch.setattr(runner, "_file_pull_session", lambda *a: order.append("pull"))
    monkeypatch.setattr(runner, "collect_usage", lambda *a: runner.UsageMetrics())
    monkeypatch.setattr(runner, "extract_mount_plan", lambda *a: (None, None))
    monkeypatch.setattr(runner, "_verify_workspace", lambda *a: order.append("verify") or (None, "", 0.0))

    @contextmanager
    def proxy(private_dir, base, key, bind, record):
        assert base == "https://api.example.test/v1" and key == FAKE_KEY
        assert bind == "10.0.0.1"
        assert stat.S_IMODE(private_dir.stat().st_mode) == 0o700
        order.append("proxy_start")
        record(IDENTIFIER, "active")
        try:
            yield PROXY_URL, FAKE_TOKEN
        finally:
            order.append("proxy_cleanup")
            if cleanup_error:
                raise RuntimeError(FAKE_KEY)
            record(IDENTIFIER, "reaped")

    monkeypatch.setattr(runner, "credential_proxy", proxy)

    def execute(*args, **kwargs):
        order.append("exec")
        assert kwargs["provider_env"] == {"OPENAI_API_KEY": FAKE_TOKEN, "OPENAI_BASE_URL": PROXY_URL}
        assert FAKE_KEY not in repr(kwargs)
        if exec_error is not None:
            raise exec_error
        return 0, '{"status":"ok"}', "", 2.0

    monkeypatch.setattr(runner, "_exec_amplifier", execute)
    return order, launch


def gitea():
    return GiteaSession("fake-gitea", 12345, "http://localhost:12345", "FAKE_GITEA_TOKEN")


def test_run_one_proxy_cleanup_precedes_collection(tmp_path, monkeypatch, fake_host):
    order, launch = mock_infra(monkeypatch)
    spec = spec_for(tmp_path)
    run_dir = tmp_path / "run"
    result = runner.run_one(spec, gitea(), run_dir)
    assert result.exit_code == 0 and result.error is None
    assert result.extras["credential_proxy"] == {"cleanup": "confirmed", "execution": "returned"}
    assert order == ["proxy_start", "exec", "proxy_cleanup", "pull", "verify", "destroy"]
    assert launch.call_args.kwargs["clean_env"] is True
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    journal = run_dir / "proxy-resources.jsonl"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert [json.loads(s)["status"] for s in journal.read_text().splitlines()] == ["active", "reaped"]
    assert FAKE_KEY not in repr(result)
    assert FAKE_KEY not in (run_dir / "profile.yaml").read_text()


def test_timeout_cleans_proxy_and_still_pulls(tmp_path, monkeypatch, fake_host):
    order, _ = mock_infra(monkeypatch, exec_error=subprocess.TimeoutExpired("fake", 1))
    result = runner.run_one(spec_for(tmp_path), gitea(), tmp_path / "run")
    assert result.error == "CREDENTIAL_PROXY_RUN_FAILED"
    assert result.extras["credential_proxy"] == {"cleanup": "confirmed", "execution": "raised"}
    assert order == ["proxy_start", "exec", "proxy_cleanup", "pull", "destroy"]


def test_cleanup_failure_does_not_erase_cli_return_or_leak_exception(tmp_path, monkeypatch, fake_host, capsys):
    order, _ = mock_infra(monkeypatch, cleanup_error=True)
    result = runner.run_one(spec_for(tmp_path), gitea(), tmp_path / "run")
    assert result.exit_code == 0 and result.json_trace == {"status": "ok"}
    assert result.error == "CREDENTIAL_PROXY_RUN_FAILED"
    assert result.extras["credential_proxy"]["cleanup"] == "unverified"
    assert "pull" in order and "verify" not in order
    captured = capsys.readouterr()
    assert FAKE_KEY not in repr(result) + captured.out + captured.err


def test_missing_host_env_refuses_before_launch(tmp_path, monkeypatch):
    launch = Mock()
    monkeypatch.setattr(runner, "_launch_dtu", launch)
    monkeypatch.delenv("TEST_VENDOR_KEY", raising=False)
    result = runner.run_one(spec_for(tmp_path), gitea(), tmp_path / "run")
    assert result.error == "CREDENTIAL_PROXY_RUN_FAILED"
    launch.assert_not_called()


def test_run_directory_cannot_be_reused(tmp_path, monkeypatch, fake_host):
    mock_infra(monkeypatch)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sentinel = run_dir / "sentinel"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        runner.run_one(spec_for(tmp_path), gitea(), run_dir)
    assert sentinel.read_text() == "preserve"


def test_inline_upstream_key_in_overlay_refuses_before_launch(tmp_path, monkeypatch, fake_host):
    launch = Mock()
    monkeypatch.setattr(runner, "_launch_dtu", launch)
    spec = spec_for(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(f"providers:\n  api_key: {FAKE_KEY}\n")
    spec.config.settings_overlay = overlay
    result = runner.run_one(spec, gitea(), tmp_path / "run")
    assert result.error == "CREDENTIAL_PROXY_RUN_FAILED"
    launch.assert_not_called()
    assert not (tmp_path / "run" / "profile.yaml").exists()


def test_rendered_profile_removes_all_key_passthrough(tmp_path):
    spec = spec_for(tmp_path)
    profile = yaml.safe_load(render_profile(spec, "http://localhost:12345", "FAKE_GITEA_TOKEN").yaml_text)
    assert profile["passthrough"] == {"allow_external": True, "services": []}
    spec.config.credential_proxy = None
    legacy = yaml.safe_load(render_profile(spec, "http://localhost:12345", "FAKE_GITEA_TOKEN").yaml_text)
    assert legacy["passthrough"]["services"]


def test_exec_command_contains_only_temporary_credentials(monkeypatch):
    seen = []

    def run(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps({"exit_code": 0, "stdout": "{}", "stderr": ""}), "")

    monkeypatch.setattr(runner, "_run_dtu", run)
    assert runner._exec_amplifier(
        "fake-dtu", bundle_name="test-bundle", prompt="hello", timeout_s=10,
        provider_env={"OPENAI_API_KEY": FAKE_TOKEN, "OPENAI_BASE_URL": PROXY_URL},
    )[0] == 0
    assert seen[0][1]["clean_env"] is True
    command = seen[0][0][-1]
    assert "env -i HOME=/root" in command and FAKE_TOKEN in command and PROXY_URL in command
    assert FAKE_KEY not in command


def test_clean_dtu_subprocess_does_not_receive_host_credentials(monkeypatch, fake_host):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(subprocess, "run", run)
    runner._run_dtu(["launch", "fake-profile"], clean_env=True)
    env = run.call_args.kwargs["env"]
    assert not set(env) - {"PATH", "HOME", "LANG", "LC_ALL"}
    assert FAKE_KEY not in repr(env) and "OTHER_VENDOR_SECRET" not in env


@pytest.mark.parametrize("value", ["0.0.0.0", "8.8.8.8", "not-an-address"])
def test_proxy_bind_address_validation(value):
    with pytest.raises(ValueError):
        CredentialProxyConfig(value)


def test_config_load_uses_references_without_reading_values(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "bundles": [{"name": "test", "source": "git+https://github.com/microsoft/amplifier-foundation"}],
        "scenarios": ["fixture"],
        "credential_proxy": {"bind_ipv4": "10.0.0.1", "upstream_key_env": "TEST_VENDOR_KEY"},
    }))
    monkeypatch.delenv("TEST_VENDOR_KEY", raising=False)
    assert load_config(path).credential_proxy.upstream_key_env == "TEST_VENDOR_KEY"
    data = yaml.safe_load(path.read_text())
    data["credential_proxy"]["api_key"] = FAKE_KEY
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="environment references"):
        load_config(path)


def test_refuses_value_in_place_of_env_name():
    with pytest.raises(ValueError):
        CredentialProxyConfig("10.0.0.1", "sk-not-an-env-reference")


@pytest.mark.parametrize("name", ["PATH", "HOME", "LANG", "LC_ALL"])
def test_refuses_env_reference_colliding_with_subprocess_allowlist(name):
    with pytest.raises(ValueError, match="allowlist"):
        CredentialProxyConfig("10.0.0.1", upstream_key_env=name)
    with pytest.raises(ValueError, match="allowlist"):
        CredentialProxyConfig("10.0.0.1", upstream_base_url_env=name)


def test_refuses_upstream_key_in_launch_variables(tmp_path, monkeypatch, fake_host):
    launch = Mock()
    monkeypatch.setattr(runner, "_launch_dtu", launch)
    unsafe_gitea = GiteaSession("fake-gitea", 12345, "http://localhost:12345", FAKE_KEY)
    result = runner.run_one(spec_for(tmp_path), unsafe_gitea, tmp_path / "run")
    assert result.error == "CREDENTIAL_PROXY_RUN_FAILED"
    launch.assert_not_called()


def test_cli_scheduler_uses_proxy_runner_and_persists_cleanup_states(tmp_path, monkeypatch, fake_host):
    from click.testing import CliRunner

    from amplifier_eval_harness import cli

    spec = spec_for(tmp_path)
    config = tmp_path / "config.yaml"
    output = tmp_path / "results"
    config.write_text(yaml.safe_dump({
        "profile": str(spec.config.profile_template),
        "output_dir": str(output),
        "bundles": [{"name": spec.bundle.name, "source": spec.bundle.source}],
        "scenarios": [{"id": "fixture", "prompt_path": str(spec.scenario.prompt_path)}],
        "credential_proxy": {
            "bind_ipv4": "10.0.0.1",
            "upstream_key_env": "TEST_VENDOR_KEY",
            "upstream_base_url_env": "TEST_VENDOR_BASE",
        },
    }))
    order, _ = mock_infra(monkeypatch)
    monkeypatch.setattr(cli, "ensure_gitea", lambda **kw: gitea())
    monkeypatch.setattr(cli, "populate_repo", lambda *a, **kw: None)
    invocation = CliRunner().invoke(cli.main, ["run", "--config", str(config)])
    assert invocation.exit_code == 0, invocation.output
    assert order[:4] == ["proxy_start", "exec", "proxy_cleanup", "pull"]
    run_dir = output / "runs" / f"{spec.bundle.name}__fixture__r1"
    info = json.loads((run_dir / "dtu-info.json").read_text())
    assert info["status"] == "success"
    assert (run_dir / "exit_code").read_text().strip() == "0"
    assert info["extras"]["credential_proxy"] == {"cleanup": "confirmed", "execution": "returned"}
    assert FAKE_KEY not in invocation.output
    assert all(FAKE_KEY.encode() not in p.read_bytes() for p in output.rglob("*") if p.is_file())
