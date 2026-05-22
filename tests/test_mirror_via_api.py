"""Tests for _mirror_via_api() and the external-session code path in mirror_from_github().

The direct REST API path replaces the ``amplifier-gitea mirror-from-github`` CLI
in no-Docker environments (Resolve workers running in Incus).
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from amplifier_eval_harness.gitea import GiteaSession, _mirror_via_api, mirror_from_github

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def external_session() -> GiteaSession:
    """A GiteaSession as returned by the env-var bypass (instance_id='external')."""
    return GiteaSession(
        instance_id="external",
        port=55807,
        url="http://10.119.176.237:55807",
        token="tok-test",
    )


@pytest.fixture()
def docker_session() -> GiteaSession:
    """A GiteaSession backed by a local Docker Gitea (normal, non-external path)."""
    return GiteaSession(
        instance_id="g-abc123",
        port=10110,
        url="http://localhost:10110",
        token="tok-local",
    )


# ---------------------------------------------------------------------------
# 1. _mirror_via_api calls the correct endpoint with correct headers + body
# ---------------------------------------------------------------------------


def test_mirror_via_api_calls_correct_endpoint(external_session: GiteaSession) -> None:
    """_mirror_via_api POSTs to /api/v1/repos/migrate with correct payload."""
    github_url = "https://github.com/microsoft/amplifier-foundation"

    # Build a fake 201 response
    fake_resp = MagicMock()
    fake_resp.status = 201
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _mirror_via_api(external_session, github_url)

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]

    # Verify URL
    assert req.full_url == f"{external_session.url}/api/v1/repos/migrate"

    # Verify headers
    assert req.get_header("Content-type") == "application/json"
    assert req.get_header("Authorization") == f"token {external_session.token}"

    # Verify body / payload
    payload = json.loads(req.data)
    assert payload["clone_addr"] == github_url
    assert payload["repo_owner"] == "admin"
    assert payload["repo_name"] == "amplifier-foundation"
    assert payload["mirror"] is False


# ---------------------------------------------------------------------------
# 2. HTTP 409 (already exists) is treated as success
# ---------------------------------------------------------------------------


def test_mirror_via_api_accepts_409_as_success(external_session: GiteaSession) -> None:
    """A 409 response from Gitea migrate API is not an error."""
    import urllib.error

    exc_409 = urllib.error.HTTPError(url="", code=409, msg="Conflict", hdrs=None, fp=BytesIO(b""))  # type: ignore[arg-type]

    with patch("urllib.request.urlopen", side_effect=exc_409):
        # Should NOT raise
        _mirror_via_api(external_session, "https://github.com/org/repo")


# ---------------------------------------------------------------------------
# 3. HTTP 500 raises RuntimeError
# ---------------------------------------------------------------------------


def test_mirror_via_api_raises_on_unexpected_status(external_session: GiteaSession) -> None:
    """Any status other than 201 or 409 raises RuntimeError."""
    import urllib.error

    exc_500 = urllib.error.HTTPError(url="", code=500, msg="Server Error", hdrs=None, fp=BytesIO(b""))  # type: ignore[arg-type]

    with patch("urllib.request.urlopen", side_effect=exc_500):
        with pytest.raises(RuntimeError, match="unexpected HTTP 500"):
            _mirror_via_api(external_session, "https://github.com/org/repo")


# ---------------------------------------------------------------------------
# 4. mirror_from_github uses API path for external session, NOT the CLI
# ---------------------------------------------------------------------------


def test_mirror_via_api_uses_api_path_for_external_session(
    external_session: GiteaSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env bypass is active, mirror_from_github must NOT call subprocess (the CLI)."""
    monkeypatch.setenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", external_session.url)

    # Repo doesn't pre-exist
    with (
        patch("amplifier_eval_harness.gitea._gitea_repo_exists", return_value=False),
        patch("amplifier_eval_harness.gitea._wait_for_mirror_ready"),
        patch("amplifier_eval_harness.gitea._mirror_via_api") as mock_api,
        patch("subprocess.run") as mock_subprocess,
    ):
        mirror_from_github(external_session, "https://github.com/org/repo")

    # API function should have been called
    assert mock_api.called, "_mirror_via_api should have been called for external session"
    # CLI subprocess should NOT have been called
    assert not mock_subprocess.called, "subprocess.run should NOT be called for external session"


# ---------------------------------------------------------------------------
# 5. mirror_from_github uses CLI path for normal (Docker) session
# ---------------------------------------------------------------------------


def test_mirror_from_github_uses_cli_for_docker_session(
    docker_session: GiteaSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no env bypass, mirror_from_github uses the CLI subprocess (legacy path)."""
    monkeypatch.delenv("AMPLIFIER_EVAL_HARNESS_GITEA_URL", raising=False)

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = ""
    fake_result.stderr = ""

    with (
        patch("amplifier_eval_harness.gitea._gitea_repo_exists", return_value=False),
        patch("amplifier_eval_harness.gitea._wait_for_mirror_ready"),
        patch("amplifier_eval_harness.gitea._mirror_via_api") as mock_api,
        patch("subprocess.run", return_value=fake_result) as mock_subprocess,
    ):
        mirror_from_github(docker_session, "https://github.com/org/repo")

    # API function should NOT have been called for the Docker path
    assert not mock_api.called, "_mirror_via_api should NOT be called for normal Docker session"
    # CLI subprocess SHOULD have been called
    assert mock_subprocess.called, "subprocess.run should be called for normal Docker session"


# ---------------------------------------------------------------------------
# 6. _mirror_via_api uses github_token in payload when provided
# ---------------------------------------------------------------------------


def test_mirror_via_api_includes_github_token_in_payload(external_session: GiteaSession) -> None:
    """When github_token is provided, it is included in the migrate API request body."""
    fake_resp = MagicMock()
    fake_resp.status = 201
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _mirror_via_api(external_session, "https://github.com/org/private-repo", github_token="ghp_secret")

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data)
    assert payload["auth_token"] == "ghp_secret"


# ---------------------------------------------------------------------------
# 7. repo_name is correctly extracted from the GitHub URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "github_url,expected_repo_name",
    [
        ("https://github.com/org/my-repo", "my-repo"),
        ("https://github.com/org/my-repo.git", "my-repo"),
        ("https://github.com/org/my-repo/", "my-repo"),
        ("https://github.com/org/my-repo.git/", "my-repo"),
    ],
)
def test_mirror_via_api_extracts_repo_name_correctly(
    external_session: GiteaSession,
    github_url: str,
    expected_repo_name: str,
) -> None:
    """repo_name is correctly extracted from various GitHub URL forms."""
    fake_resp = MagicMock()
    fake_resp.status = 201
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _mirror_via_api(external_session, github_url)

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data)
    assert payload["repo_name"] == expected_repo_name, (
        f"Expected repo_name={expected_repo_name!r} for URL {github_url!r}, got {payload['repo_name']!r}"
    )
