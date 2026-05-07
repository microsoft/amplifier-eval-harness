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
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GiteaSession:
    """A live Gitea instance + auth, used for the lifetime of one harness run."""

    instance_id: str  # e.g. "gitea-a1b2c3d4"
    port: int  # host port (e.g. 10110)
    url: str  # "http://localhost:<port>" — passed to DTU as GITEA_URL var
    token: str  # API token, valid for the harness session

    @property
    def admin_user(self) -> str:
        return "admin"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str], *, capture: bool = True, check: bool = True, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    """Run a command, raising on non-zero by default. Returns CompletedProcess."""
    print(f"+ {' '.join(shlex.quote(c) for c in cmd)}", file=sys.stderr)
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        cwd=str(cwd) if cwd else None,
    )


def _run_shell(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a multi-line shell script via bash -lc."""
    print(f"+ bash -lc <<<\n{script}", file=sys.stderr)
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
    """Find an existing Gitea instance or create a new one. Always returns a fresh token.

    Per amplifier-tester convention: reuse instances aggressively — don't churn
    the Gitea container between harness sessions.
    """
    # Step 1: list existing
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
        print(f"Reusing existing Gitea instance: {instance_id} on port {port}", file=sys.stderr)
    else:
        # Step 2: create
        print(f"No existing Gitea instance found; creating one on port {preferred_port}...", file=sys.stderr)
        result = _run(["amplifier-gitea", "create", "--port", str(preferred_port)])
        created = json.loads(result.stdout)
        instance_id = created["id"]
        port = int(created.get("port", preferred_port))
        # The create response includes a token, but we'll regenerate to be uniform.

    # Step 3: generate a fresh token (works for both reuse and create paths)
    result = _run(["amplifier-gitea", "token", instance_id])
    token_data = json.loads(result.stdout)
    token = token_data.get("token") or token_data.get("api_token")
    if not token:
        # Some versions return the token directly as a string
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
    """Return True if admin/<repo_name> exists in Gitea."""
    cmd = [
        "curl",
        "-sf",
        "-H",
        f"Authorization: token {session.token}",
        f"{session.url}/api/v1/repos/admin/{repo_name}",
    ]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def mirror_from_github(session: GiteaSession, github_url: str, *, github_token: str | None = None) -> None:
    """Mirror a GitHub repo to Gitea (one-shot snapshot, mirror=False).

    Idempotent via existence check first.
    """
    repo_name = github_url.rstrip("/").split("/")[-1].removesuffix(".git")
    if _gitea_repo_exists(session, repo_name):
        print(f"  → {repo_name} already in Gitea, skipping mirror.", file=sys.stderr)
        return
    cmd = ["amplifier-gitea", "mirror-from-github", session.instance_id, "--github-repo", github_url]
    if github_token:
        cmd.extend(["--github-token", github_token])
    _run(cmd)
    print(f"  → mirrored {github_url} → admin/{repo_name}", file=sys.stderr)


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

    # Ensure the target Gitea repo exists (create if needed; we can't push to nothing).
    # If admin/<repo_name> doesn't exist, create an empty one via API.
    if not _gitea_repo_exists(session, repo_name):
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
            f"{session.url}/api/v1/admin/users/{session.admin_user}/repos",
        ]
        result = subprocess.run(create_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Try the user-scoped API as a fallback
            create_cmd_alt = [
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
                f"{session.url}/api/v1/user/repos",
            ]
            result = subprocess.run(create_cmd_alt, capture_output=True, text=True, check=True)
        print(f"  → created empty admin/{repo_name} in Gitea", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="eval-harness-snap-") as tmp:
        snap = Path(tmp) / repo_name
        # The bash here is a literal port of amplifier-tester's snapshot procedure.
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
    print(f"  → snapshot-pushed {local_path} → admin/{repo_name}", file=sys.stderr)


def populate_repo(session: GiteaSession, *, repo_owner: str, repo_name: str, local_path: Path | None) -> None:
    """Ensure admin/<repo_name> in Gitea reflects the right source.

    - If local_path is set: snapshot-push the working tree.
    - Otherwise: mirror from github.com/<repo_owner>/<repo_name> (idempotent).
    """
    if local_path is not None:
        snapshot_push(session, local_path, repo_name)
    else:
        github_url = f"https://github.com/{repo_owner}/{repo_name}"
        # Try to use gh's token for private/rate-limit avoidance.
        gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        mirror_from_github(session, github_url, github_token=gh_token)
