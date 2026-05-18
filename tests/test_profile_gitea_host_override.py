"""Regression tests: AMPLIFIER_EVAL_HARNESS_GITEA_HOST rewrite for nested-DTU Gitea access.

Root cause: when amplifier-eval-harness runs inside an Incus DTU and spawns eval-sub-DTUs
as siblings via a forwarded incus socket, the sub-DTUs' "localhost" is their own loopback —
not the harness DTU's. The hardcoded "http://localhost:<port>" GITEA_URL baked into sub-DTU
profiles resolves to nowhere, so ``uv tool install`` in sub-DTUs fails on any transitive
git+https://github.com/microsoft/... dependency (url_rewrites redirect to an unreachable host).

The fix: _resolve_dtu_gitea_url() rewrites the host portion of gitea_url for sub-DTU launch
vars when AMPLIFIER_EVAL_HARNESS_GITEA_HOST is set. Local harness Gitea operations (API calls,
mirroring, token fetches) are NOT affected — they still use GiteaSession.url (localhost).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_eval_harness.config import BundleSpec, RunConfig, RunSpec, ScenarioSpec
from amplifier_eval_harness.gitea import GiteaSession
from amplifier_eval_harness.profile import _resolve_dtu_gitea_url, render_profile

# ---------------------------------------------------------------------------
# _resolve_dtu_gitea_url unit tests (pure function)
# ---------------------------------------------------------------------------


def test_host_override_rewrites_gitea_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """With env var set, the host portion of gitea_url is rewritten for sub-DTUs."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", "10.119.176.124")
    result = _resolve_dtu_gitea_url("http://localhost:10110")
    assert result == "http://10.119.176.124:10110", f"Expected host rewrite to 10.119.176.124:10110, got: {result!r}"


def test_no_override_returns_url_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without env var, gitea_url is returned unmodified (no regression)."""
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", raising=False)
    original = "http://localhost:10110"
    result = _resolve_dtu_gitea_url(original)
    assert result == original, f"URL should be unchanged when env var is absent. Got: {result!r}"


def test_empty_env_var_returns_url_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty (but set) env var is treated as absent — no rewrite."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", "")
    original = "http://localhost:10110"
    result = _resolve_dtu_gitea_url(original)
    assert result == original, f"Empty env var should not trigger rewrite. Got: {result!r}"


def test_whitespace_only_env_var_returns_url_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whitespace-only env var is treated as absent — no rewrite."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", "   ")
    original = "http://localhost:10110"
    result = _resolve_dtu_gitea_url(original)
    assert result == original, f"Whitespace-only env var should not trigger rewrite. Got: {result!r}"


def test_host_override_preserves_scheme_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scheme and port are preserved; only the host is replaced."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", "192.168.1.100")
    result = _resolve_dtu_gitea_url("http://localhost:9876")
    assert result == "http://192.168.1.100:9876", f"Wrong result: {result!r}"


def test_host_override_url_without_explicit_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host-only URLs (no explicit port) are also handled correctly."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", "10.0.0.1")
    result = _resolve_dtu_gitea_url("http://localhost")
    assert result == "http://10.0.0.1", f"Wrong result: {result!r}"


# ---------------------------------------------------------------------------
# Integration: render_profile() propagates the rewrite into launch_vars
# ---------------------------------------------------------------------------


def _make_spec_for_profile(tmp_path: Path) -> RunSpec:
    """Minimal RunSpec with a trivial profile template (all placeholders present)."""
    template = tmp_path / "profile.yaml.tmpl"
    template.write_text(
        "# template\n"
        "{{ URL_REWRITES_RULES }}\n"
        "{{ PYPI_OVERRIDES }}\n"
        "{{ SETTINGS_OVERLAY_FILES }}\n"
        "{{ SETTINGS_OVERLAY_SETUP_CMD }}\n"
    )
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hello")
    return RunSpec(
        bundle=BundleSpec(
            name="test-bundle",
            source="git+https://github.com/microsoft/amplifier-foundation",
        ),
        scenario=ScenarioSpec(
            id="test-scenario",
            prompt_path=prompt_file,
            workspace_path=None,
        ),
        run_index=1,
        config=RunConfig(
            config_path=tmp_path / "config.yaml",
            output_dir=tmp_path / "output",
            profile_template=template,
            exec_timeout_s=60,
            launch_timeout_s=60,
            keep_dtu_on_failure=False,
            keep_dtu_on_success=False,
        ),
    )


def test_render_profile_uses_override_host_in_launch_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """render_profile() must apply host override to GITEA_URL in launch_vars."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", "10.119.176.124")
    spec = _make_spec_for_profile(tmp_path)
    rendered = render_profile(spec, gitea_url="http://localhost:10110", gitea_token="test-token")
    assert rendered.launch_vars["GITEA_URL"] == "http://10.119.176.124:10110", (
        f"Expected overridden GITEA_URL in launch_vars, got: {rendered.launch_vars['GITEA_URL']!r}"
    )


def test_render_profile_no_override_preserves_original_launch_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without env var, GITEA_URL in launch_vars is unchanged (regression guard)."""
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", raising=False)
    spec = _make_spec_for_profile(tmp_path)
    rendered = render_profile(spec, gitea_url="http://localhost:10110", gitea_token="test-token")
    assert rendered.launch_vars["GITEA_URL"] == "http://localhost:10110", (
        f"Expected unmodified GITEA_URL without env var, got: {rendered.launch_vars['GITEA_URL']!r}"
    )


def test_local_gitea_session_url_not_affected_by_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """GiteaSession.url (used for local API calls) must not be affected by the env var.

    This asserts the architectural separation: the env var only affects the GITEA_URL
    passed to sub-DTU launch vars. GiteaSession.url is used for the harness's own
    Gitea API calls (mirroring, token fetches, existence checks, etc.) and must remain
    as-is from the harness's own network perspective.
    """
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_HOST", "10.119.176.124")
    session = GiteaSession(instance_id="g-test", port=10110, url="http://localhost:10110", token="tok")
    # The session.url must remain unchanged — local ops use this directly.
    assert session.url == "http://localhost:10110", (
        f"GiteaSession.url must not be modified by the env var. Got: {session.url!r}"
    )
