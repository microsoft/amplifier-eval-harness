"""Per-run flow: launch DTU, exec amplifier, pull session, destroy.

Each call to run_one() is independent and may run concurrently in a worker thread.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._log import clear_prefix, log, set_prefix
from .config import RunSpec
from .gitea import GiteaSession
from .profile import RenderedProfile, render_profile, write_profile


@dataclass
class RunResult:
    """Outcome of one run."""

    run_id: str
    dtu_instance_id: str | None = None
    profile_path: Path | None = None
    launch_seconds: float = 0.0
    readiness_seconds: float = 0.0
    exec_seconds: float = 0.0
    teardown_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    exit_code: int | None = None
    json_trace: dict[str, Any] | None = None
    raw_stdout: str = ""
    raw_stderr: str = ""
    error: str | None = None  # populated on harness-side failures (launch/exec/teardown)
    started_at: str = ""
    ended_at: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """High-level outcome: 'success' | 'amplifier_error' | 'harness_error' | 'unknown'."""
        if self.error:
            return "harness_error"
        if self.exit_code is None:
            return "unknown"
        if self.exit_code != 0:
            return "amplifier_error"
        if self.json_trace and self.json_trace.get("status") == "error":
            return "amplifier_error"
        return "success"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run_dtu(args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    cmd = ["amplifier-digital-twin", *args]
    log(f"+ {' '.join(shlex.quote(c) for c in cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def _launch_dtu(profile_path: Path, launch_vars: dict[str, str], *, timeout_s: int) -> tuple[str, float]:
    """Launch a DTU from the profile, return (instance_id, elapsed_seconds)."""
    args = ["launch", str(profile_path)]
    for k, v in launch_vars.items():
        args.extend(["--var", f"{k}={v}"])
    t0 = time.time()
    result = _run_dtu(args, timeout=timeout_s)
    elapsed = time.time() - t0
    if result.returncode != 0:
        raise RuntimeError(
            f"DTU launch failed (rc={result.returncode}):\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
        )
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        idx = result.stdout.rfind("{")
        if idx == -1:
            raise RuntimeError(f"Could not parse launch JSON from stdout:\n{result.stdout}") from None
        info = json.loads(result.stdout[idx:])
    instance_id = info.get("id") or info.get("instance_id")
    if not instance_id:
        raise RuntimeError(f"Launch JSON missing 'id': {info!r}")
    return instance_id, elapsed


def _wait_readiness(instance_id: str, *, max_attempts: int = 60, interval_s: float = 5.0) -> float:
    """Poll readiness until ready=true. Returns seconds spent waiting."""
    t0 = time.time()
    for attempt in range(1, max_attempts + 1):
        result = _run_dtu(["check-readiness", instance_id])
        if result.returncode != 0:
            log(f"  readiness check rc={result.returncode}: {result.stderr.strip()}")
        else:
            try:
                info = json.loads(result.stdout)
                if info.get("ready"):
                    return time.time() - t0
            except json.JSONDecodeError:
                pass
        if attempt == max_attempts:
            raise RuntimeError(f"DTU {instance_id} did not reach readiness in {max_attempts * interval_s:.0f}s")
        time.sleep(interval_s)
    return time.time() - t0


def _push_workspace_fixture(instance_id: str, local_path: Path) -> None:
    """Push scenario workspace files into /workspace inside the DTU."""
    if not local_path.is_dir():
        return
    args = ["file-push", instance_id]
    for entry in local_path.iterdir():
        args.append(str(entry))
    args.append("/workspace/")
    result = _run_dtu(args, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"workspace push failed:\n{result.stderr}")


def _exec_amplifier(
    instance_id: str,
    *,
    bundle_name: str,
    prompt: str,
    timeout_s: int,
) -> tuple[int, str, str, float]:
    """Run `amplifier run --bundle <name> --output-format json-trace "<prompt>"` inside the DTU."""
    inner = (
        f"export PATH=/root/.local/bin:$PATH && "
        f"cd /workspace && "
        f"amplifier run --bundle {shlex.quote(bundle_name)} "
        f"--output-format json-trace {shlex.quote(prompt)}"
    )
    args = ["exec", instance_id, "--", "bash", "-lc", inner]
    t0 = time.time()
    result = _run_dtu(args, timeout=timeout_s)
    elapsed = time.time() - t0
    if result.returncode != 0:
        raise RuntimeError(
            f"DTU exec failed (rc={result.returncode}). "
            f"This is an infrastructure failure, not amplifier behavior.\n"
            f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
        )
    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse DTU exec JSON: {e}\nstdout was:\n{result.stdout}") from e
    return (
        int(outer.get("exit_code", -1)),
        outer.get("stdout", ""),
        outer.get("stderr", ""),
        elapsed,
    )


def _file_pull_session(instance_id: str, dest_dir: Path) -> None:
    """Pull /root/.amplifier/projects/ from the DTU into dest_dir/session/."""
    dest = dest_dir / "session"
    dest.mkdir(parents=True, exist_ok=True)
    args = ["file-pull", instance_id, "/root/.amplifier/projects/", str(dest)]
    result = _run_dtu(args, timeout=300)
    if result.returncode != 0:
        log(f"  warning: file-pull failed (rc={result.returncode}): {result.stderr.strip()}")


def _destroy_dtu(instance_id: str) -> float:
    """Destroy the DTU, returning seconds elapsed."""
    t0 = time.time()
    result = _run_dtu(["destroy", instance_id], timeout=180)
    elapsed = time.time() - t0
    if result.returncode != 0:
        log(f"  warning: destroy failed (rc={result.returncode}): {result.stderr.strip()}")
    return elapsed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_one(spec: RunSpec, gitea: GiteaSession, run_dir: Path) -> RunResult:
    """Execute one (bundle, scenario, run_index) combo.

    Always returns a RunResult, even on failure (errors captured in result.error).
    Sets a thread-local log prefix so output remains attributable when this is
    called from a worker thread under parallelism.
    """
    set_prefix(spec.run_id)
    try:
        return _run_one_inner(spec, gitea, run_dir)
    finally:
        clear_prefix()


def _run_one_inner(spec: RunSpec, gitea: GiteaSession, run_dir: Path) -> RunResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    result = RunResult(run_id=spec.run_id, started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    started_wall = time.time()

    rendered: RenderedProfile | None = None
    instance_id: str | None = None

    try:
        # 1. Render and write profile
        rendered = render_profile(spec, gitea_url=gitea.url, gitea_token=gitea.token)
        profile_path = write_profile(spec, rendered, run_dir)
        result.profile_path = profile_path

        # 2. Launch
        instance_id, elapsed = _launch_dtu(profile_path, rendered.launch_vars, timeout_s=spec.config.launch_timeout_s)
        result.dtu_instance_id = instance_id
        result.launch_seconds = elapsed

        # 3. Wait readiness
        result.readiness_seconds = _wait_readiness(instance_id)

        # 4. Push workspace fixture (if any)
        if spec.scenario.workspace_path is not None:
            _push_workspace_fixture(instance_id, spec.scenario.workspace_path)

        # 5. Exec amplifier
        prompt = spec.scenario.prompt_path.read_text()
        exit_code, stdout, stderr, exec_elapsed = _exec_amplifier(
            instance_id,
            bundle_name=spec.bundle.name,
            prompt=prompt,
            timeout_s=spec.config.exec_timeout_s,
        )
        result.exit_code = exit_code
        result.raw_stdout = stdout
        result.raw_stderr = stderr
        result.exec_seconds = exec_elapsed

        # Try to parse json-trace
        try:
            result.json_trace = json.loads(stdout)
        except json.JSONDecodeError:
            idx = stdout.rfind("{")
            if idx >= 0:
                try:
                    result.json_trace = json.loads(stdout[idx:])
                except json.JSONDecodeError:
                    result.json_trace = None

        # 6. Pull session files
        _file_pull_session(instance_id, run_dir)

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        log(f"  ERROR: {result.error}")

    finally:
        # 7. Teardown
        if instance_id is not None:
            keep = (result.error and spec.config.keep_dtu_on_failure) or (
                not result.error and spec.config.keep_dtu_on_success
            )
            if keep:
                reason = "keep_dtu_on_failure" if result.error else "keep_dtu_on_success"
                log(f"  keeping DTU {instance_id} ({reason}=true)")
            else:
                result.teardown_seconds = _destroy_dtu(instance_id)

        result.wall_clock_seconds = time.time() - started_wall
        result.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    return result
