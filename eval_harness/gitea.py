"""Gitea session manager: list/create instance, generate token, mirror upstream, snapshot push.

The harness ALWAYS routes installs through Gitea — both upstream-only and
local-override repos go through the same proxy mechanism in DTU. This module
owns the Gitea side of that pipeline.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ._log import log


@dataclass
class GiteaSession:
    """A live Gitea instance + auth, used for the lifetime of one harness run."""

    instance_id: str
    port: int
    url: str
    token: str

    @property
    def admin_user(self) -> str:
        return "admin"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str], *, capture: bool = True, check: bool = True, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    log(f"+ {' '.join(shlex.quote(c) for c in cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=False,
        cwd=str(cwd) if cwd else None,
    )
    if check and result.returncode != 0:
        # Surface captured output BEFORE raising so the user sees what went wrong.
        log(f"  command failed (rc={result.returncode})")
        if result.stdout and result.stdout.strip():
            log(f"  stdout: {result.stdout.strip()[:1000]}")
        if result.stderr and result.stderr.strip():
            log(f"  stderr: {result.stderr.strip()[:1000]}")
        result.check_returncode()  # raises CalledProcessError
    return result


def _run_shell(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    log("+ bash -lc <<<")
    log(script.strip())
    return subprocess.run(
        ["bash", "-lc", script],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(env or {})},
    )


# ---------------------------------------------------------------------------
# Gitea lifecycle
# ---------------------------------------------------------------------------


def ensure_gitea(preferred_port: int = 10110) -> GiteaSession:
    """Find an existing Gitea instance or create a new one. Always returns a fresh token."""
    result = _run(["amplifier-gitea", "list"], check=False)
    instances: list[dict] = []
    if result.returncode == 0 and result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
            if isinstance(parsed, list):
                instances = parsed
        except json.JSONDecodeError:
            pass

    if instances:
        first = instances[0]
        instance_id = first["id"]
        port = int(first.get("port", preferred_port))
        log(f"Reusing existing Gitea instance: {instance_id} on port {port}")
    else:
        log(f"No existing Gitea instance found; creating one on port {preferred_port}...")
        result = _run(["amplifier-gitea", "create", "--port", str(preferred_port)])
        created = json.loads(result.stdout)
        instance_id = created["id"]
        port = int(created.get("port", preferred_port))

    # Generate a fresh token (uniform across reuse and create paths)
    result = _run(["amplifier-gitea", "token", instance_id])
    try:
        token_data = json.loads(result.stdout)
        token = token_data.get("token") or token_data.get("api_token") or result.stdout.strip()
    except json.JSONDecodeError:
        token = result.stdout.strip()

    return GiteaSession(
        instance_id=instance_id,
        port=port,
        url=f"http://localhost:{port}",
        token=token,
    )


# ---------------------------------------------------------------------------
# Repo population: upstream mirror + local snapshot push
# ---------------------------------------------------------------------------


def _gitea_repo_exists(session: GiteaSession, repo_name: str) -> bool:
    """Return True if admin/<repo_name> exists in Gitea (HTTP 200 to its API endpoint)."""
    cmd = [
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-H",
        f"Authorization: token {session.token}",
        f"{session.url}/api/v1/repos/admin/{repo_name}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    code = (result.stdout or "").strip()
    exists = code == "200"
    log(f"  · existence check {repo_name}: HTTP {code or '?'} → {'exists' if exists else 'not found'}")
    return exists


def mirror_from_github(session: GiteaSession, github_url: str, *, github_token: str | None = None) -> None:
    """Mirror a GitHub repo to Gitea. Idempotent against pre-existing repos.

    Persistent Docker volumes mean a "fresh" Gitea instance can have repos from
    previous runs. The amplifier-gitea CLI surfaces this as a 409 from Gitea's
    migrate API. We treat any failure where the repo ends up existing as a no-op.
    """
    repo_name = github_url.rstrip("/").split("/")[-1].removesuffix(".git")

    if _gitea_repo_exists(session, repo_name):
        log(f"  → {repo_name} already in Gitea, skipping mirror.")
        return

    cmd = ["amplifier-gitea", "mirror-from-github", session.instance_id, "--github-repo", github_url]
    if github_token:
        cmd.extend(["--github-token", github_token])

    try:
        _run(cmd)
        log(f"  → mirrored {github_url} → admin/{repo_name}")
    except subprocess.CalledProcessError as e:
        if _gitea_repo_exists(session, repo_name):
            log(f"  → {repo_name} present after mirror call (likely 409 already-exists); proceeding.")
            return
        raise RuntimeError(
            f"mirror-from-github failed for {github_url} (rc={e.returncode}). "
            f"Gitea does not contain admin/{repo_name} after the call. "
            f"stdout: {(e.stdout or '').strip()[:500]} | stderr: {(e.stderr or '').strip()[:500]}"
        ) from e


def snapshot_push(session: GiteaSession, local_path: Path, repo_name: str) -> None:
    """Push a local working tree (incl. uncommitted changes, excl. .gitignore'd) to Gitea.

    Critical safety properties:
      - The user's working tree is never mutated.
      - All git mutations happen in a temp clone.
      - .gitignore'd files are NOT pushed (--exclude-standard).
      - Only HEAD is force-pushed to main.
    """
    local_path = local_path.resolve()
    if not (local_path / ".git").exists():
        raise RuntimeError(f"Not a git repo: {local_path}")

    # Ensure the target Gitea repo exists (create empty if needed; we can't push to nothing).
    if not _gitea_repo_exists(session, repo_name):
        for endpoint in (
            f"{session.url}/api/v1/admin/users/{session.admin_user}/repos",
            f"{session.url}/api/v1/user/repos",
        ):
            create_cmd = [
                "curl",
                "-sf",
                "-X",
                "POST",
                "-H",
                f"Authorization: token {session.token}",
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps({"name": repo_name, "auto_init": False}),
                endpoint,
            ]
            r = subprocess.run(create_cmd, capture_output=True, text=True)
            if r.returncode == 0:
                log(f"  → created empty admin/{repo_name} in Gitea (via {endpoint.split('/api')[-1]})")
                break
        else:
            raise RuntimeError(f"Could not create empty admin/{repo_name} in Gitea via any endpoint.")

    with tempfile.TemporaryDirectory(prefix="eval-harness-snap-") as tmp:
        snap = Path(tmp) / repo_name
        script = f"""
set -euo pipefail
SRC={shlex.quote(str(local_path))}
SNAP={shlex.quote(str(snap))}
REPO={shlex.quote(repo_name)}
GITEA_URL={shlex.quote(session.url)}
GITEA_TOKEN={shlex.quote(session.token)}

git clone --local --no-hardlinks "$SRC" "$SNAP"

(
  cd "$SRC"
  git ls-files -z --cached --modified --others --exclude-standard
) | rsync -a --files-from=- --from0 "$SRC/" "$SNAP/"

(
  cd "$SRC"
  git ls-files -z --deleted
) | (cd "$SNAP" && xargs -0 --no-run-if-empty rm -f)

cd "$SNAP"
git -c user.email=eval@local -c user.name="Eval Snapshot" add -A
git -c user.email=eval@local -c user.name="Eval Snapshot" \\
    commit --allow-empty -m "Eval harness snapshot of working tree"
git remote add gitea "$(echo "$GITEA_URL" | sed -E 's|^http://|http://admin:'"$GITEA_TOKEN"'@|')/admin/$REPO.git"
git push gitea HEAD:main --force
"""
        _run_shell(script)
    log(f"  → snapshot-pushed {local_path} → admin/{repo_name}")


def populate_repo(session: GiteaSession, *, repo_owner: str, repo_name: str, local_path: Path | None) -> None:
    """Ensure admin/<repo_name> in Gitea reflects the right source.

    - If local_path is set: snapshot-push the working tree.
    - Otherwise: mirror from github.com/<repo_owner>/<repo_name> (idempotent).
    """
    if local_path is not None:
        snapshot_push(session, local_path, repo_name)
    else:
        github_url = f"https://github.com/{repo_owner}/{repo_name}"
        gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        mirror_from_github(session, github_url, github_token=gh_token)
