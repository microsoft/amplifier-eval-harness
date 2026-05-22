"""Tests for the env-var bypass in ensure_gitea().

When AMPLIFIER_EVAL_HARNESS_GITEA_URL + a token are present in the environment,
ensure_gitea() must return an external GiteaSession immediately without touching
Docker (i.e. without calling amplifier-gitea list / create / token).
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from amplifier_eval_harness.gitea import GiteaSession, ensure_gitea

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_subprocess_assert_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch subprocess.run to raise if called — ensures Docker path is NOT taken."""

    def _boom(*args, **kwargs):
        raise AssertionError(
            "subprocess.run was called — the Docker code path was taken unexpectedly. "
            f"Command: {args[0] if args else '?'}"
        )

    monkeypatch.setattr(subprocess, "run", _boom)


# ---------------------------------------------------------------------------
# 1. URL + TOKEN both set → returns external session, no subprocess
# ---------------------------------------------------------------------------


def test_ensure_gitea_uses_external_url_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """With URL + TOKEN set, ensure_gitea returns an external session without calling Docker."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "http://10.119.176.237:55807")
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", "tok-abc123")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN_ENV", raising=False)
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID", raising=False)
    _patch_subprocess_assert_not_called(monkeypatch)

    session = ensure_gitea()

    assert isinstance(session, GiteaSession)
    assert session.url == "http://10.119.176.237:55807"
    assert session.token == "tok-abc123"
    assert session.port == 55807
    assert session.instance_id == "external"


# ---------------------------------------------------------------------------
# 2. URL + TOKEN_ENV → resolves token through indirection
# ---------------------------------------------------------------------------


def test_ensure_gitea_uses_external_url_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """TOKEN_ENV pointing to another env var resolves to the token value."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "http://10.0.0.5:10110")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", raising=False)
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN_ENV", "MY_GITEA_TOKEN")
    monkeypatch.setenv("MY_GITEA_TOKEN", "resolved-token-xyz")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID", raising=False)
    _patch_subprocess_assert_not_called(monkeypatch)

    session = ensure_gitea()

    assert session.token == "resolved-token-xyz"
    assert session.url == "http://10.0.0.5:10110"


# ---------------------------------------------------------------------------
# 3. URL set but NO token → falls through to Docker path (which fails loudly)
# ---------------------------------------------------------------------------


def test_ensure_gitea_external_url_no_token_falls_back_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only URL (no token) is not sufficient for bypass — must fall through to Docker path."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "http://10.0.0.5:10110")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", raising=False)
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN_ENV", raising=False)

    # Patch the Docker CLI call to raise CalledProcessError — confirms Docker path was reached.
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "amplifier-gitea list", stderr="no docker")
        # We expect the harness to reach the Docker path and fail
        with pytest.raises((subprocess.CalledProcessError, RuntimeError, Exception)):
            ensure_gitea()

    # Verify subprocess.run WAS called (i.e. the Docker path was entered)
    assert mock_run.called, "subprocess.run should have been called (Docker path should have been entered)"


# ---------------------------------------------------------------------------
# 4. INSTANCE_ID env var missing → defaults to "external"
# ---------------------------------------------------------------------------


def test_ensure_gitea_external_url_default_instance_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID is absent, instance_id defaults to 'external'."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "http://10.0.0.1:9999")
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", "tok")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID", raising=False)
    _patch_subprocess_assert_not_called(monkeypatch)

    session = ensure_gitea()
    assert session.instance_id == "external"


# ---------------------------------------------------------------------------
# 5. URL with explicit port → session.port matches
# ---------------------------------------------------------------------------


def test_ensure_gitea_external_parses_port_from_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Port is parsed from the URL and stored in session.port."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "http://192.168.1.10:8080")
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", "tok")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID", raising=False)
    _patch_subprocess_assert_not_called(monkeypatch)

    session = ensure_gitea()
    assert session.port == 8080


# ---------------------------------------------------------------------------
# 6. URL with no port → default 80 for http
# ---------------------------------------------------------------------------


def test_ensure_gitea_external_no_port_in_url_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """URL without explicit port defaults to 80 for http."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "http://somehost/")
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", "tok")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID", raising=False)
    _patch_subprocess_assert_not_called(monkeypatch)

    session = ensure_gitea()
    assert session.port == 80


# ---------------------------------------------------------------------------
# 7. HTTPS URL with no port → default 443
# ---------------------------------------------------------------------------


def test_ensure_gitea_external_https_no_port_uses_443(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPS URL without explicit port defaults to 443."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "https://gitea.example.com")
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", "tok")
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID", raising=False)
    _patch_subprocess_assert_not_called(monkeypatch)

    session = ensure_gitea()
    assert session.port == 443


# ---------------------------------------------------------------------------
# 8. Custom INSTANCE_ID env var is respected
# ---------------------------------------------------------------------------


def test_ensure_gitea_external_custom_instance_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID is set, it is used as instance_id."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", "http://10.0.0.1:10110")
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_TOKEN", "tok")
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_INSTANCE_ID", "g-myinstance42")
    _patch_subprocess_assert_not_called(monkeypatch)

    session = ensure_gitea()
    assert session.instance_id == "g-myinstance42"
