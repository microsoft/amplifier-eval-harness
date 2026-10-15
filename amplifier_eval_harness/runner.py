"""Per-run flow: launch DTU, exec amplifier, pull session, destroy.

Each call to run_one() is independent and may run concurrently in a worker thread.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._log import clear_prefix, log, set_prefix
from .config import RunSpec
from .credential_proxy import credential_proxy, render_nginx_config
from .gitea import GiteaSession
from .mount_plan import extract_mount_plan
from .profile import RenderedProfile, render_profile, write_profile
from .usage import UsageMetrics, collect_usage

# CSI escape sequences (the main offender — colors, cursor moves, screen
# clears) plus OSC sequences (used for hyperlinks, window titles). Together
# these cover what amplifier's streaming-UI hook (and most rich/colour
# libraries) emit to stderr. We strip them defensively from captured stderr
# because the inner amplifier process does NOT respect NO_COLOR/TERM=dumb
# universally — Rich-backed printers configured with force_terminal=True (or
# similar) bypass those signals entirely.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def _strip_ansi(s: str) -> str:
    """Strip ANSI escape sequences for plain-text archival of captured output."""
    return _ANSI_RE.sub("", s)


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
    # Post-agent verification: pytest run inside /workspace AFTER the agent
    # finishes. This is what catches "amplifier exited 0 but the task
    # actually failed" — agent claimed success in its response but tests
    # don't pass / refactor regressed something / spec impl is wrong.
    #   verify_exit_code = None   → no test files in /workspace; verify skipped
    #   verify_exit_code = 0      → pytest passed
    #   verify_exit_code != 0     → pytest failed (1=fails, 2=collection/import error, etc.)
    verify_exit_code: int | None = None
    verify_summary: str = ""
    verify_seconds: float = 0.0
    # LLM usage aggregated from events.jsonl across parent + all sub-sessions.
    # Populated post-pull; defaults to a zero-value record if no events were found.
    usage: UsageMetrics = field(default_factory=UsageMetrics)
    # Resolved mount plan extracted from the root session's events.jsonl
    # (requires session.raw=true, forced by the harness's force-raw overlay).
    # The dict is the post-overlay, post-force-composition view of providers /
    # tools / hooks / agents / orchestrator / context that was actually
    # mounted. The sha256 of the canonical JSON goes into summary.csv for
    # drift detection across runs.
    mount_plan: dict[str, Any] | None = None
    mount_plan_sha: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """High-level outcome.

        Returns one of:
          'success'         — amplifier exited 0 AND verify passed (or was skipped)
          'task_failed'     — amplifier exited 0 BUT pytest reported failed tests
          'verification_error' — pytest could not run/collect/complete normally
          'collection_error' — session transfer failed; telemetry is not complete
          'amplifier_error' — amplifier exited non-zero, or its json-trace status is 'error'
          'harness_error'   — DTU launch/exec/teardown blew up
          'unknown'         — exit code wasn't captured at all
        """
        if self.error:
            return "harness_error"
        if self.exit_code is None:
            return "unknown"
        if self.exit_code != 0:
            return "amplifier_error"
        if self.json_trace and self.json_trace.get("status") == "error":
            return "amplifier_error"
        if self.extras.get("session_pull") == "failed":
            return "collection_error"
        if self.verify_exit_code is not None and self.verify_exit_code not in (0, 1):
            return "verification_error"
        if self.verify_exit_code == 1:
            return "task_failed"
        return "success"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run_dtu(
    args: list[str], *, timeout: int | None = None, clean_env: bool = False
) -> subprocess.CompletedProcess:
    cmd = ["amplifier-digital-twin", *args]
    log(f"+ {' '.join(shlex.quote(c) for c in cmd)}")
    kwargs = {}
    if clean_env:
        kwargs["env"] = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL")}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


def _extract_json_trace(stdout: str) -> dict[str, Any] | None:
    """Parse the json-trace from amplifier's stdout.

    The full payload is a single top-level JSON object. Provisioning logs or
    banners that get prefixed onto stdout would break a naive json.loads, so
    locate the first `{` and parse from there. The last `{` is wrong — it's
    just the start of the nested `metadata` object.
    """
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        first = text.find("{")
        if first < 0:
            return None
        try:
            # Use raw_decode so we don't choke on trailing newlines or extra logs.
            obj, _ = json.JSONDecoder().raw_decode(text[first:])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def _launch_dtu(
    profile_path: Path, launch_vars: dict[str, str], *, timeout_s: int, clean_env: bool = False
) -> tuple[str, float]:
    """Launch a DTU from the profile, return (instance_id, elapsed_seconds)."""
    args = ["launch", str(profile_path)]
    for k, v in launch_vars.items():
        args.extend(["--var", f"{k}={v}"])
    t0 = time.time()
    result = _run_dtu(args, timeout=timeout_s, **({"clean_env": True} if clean_env else {}))
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


def _push_workspace_fixture(
    instance_id: str, local_path: Path, *, workspace_path: str = "/workspace"
) -> None:
    """Push scenario workspace files into /workspace inside the DTU."""
    if not local_path.is_dir():
        return
    entries = sorted(local_path.iterdir())
    if not entries:
        return
    args = ["file-push", "-r", instance_id]
    for entry in entries:
        args.append(str(entry))
    args.append(workspace_path.rstrip("/") + "/")
    result = _run_dtu(args, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"workspace push failed:\n{result.stderr}")


def _exec_amplifier(
    instance_id: str,
    *,
    bundle_name: str,
    prompt: str,
    timeout_s: int,
    provider_env: dict[str, str] | None = None,
) -> tuple[int, str, str, float]:
    """Run `amplifier run --bundle <name> --output-format json-trace "<prompt>"` inside the DTU."""
    # Disable ANSI escape sequences in amplifier's output so the captured
    # stderr.log is plain text. The streaming-UI hook (and any other rich-
    # backed printer) writes ANSI color codes by default; under
    # `amplifier-digital-twin exec` the inner process can't infer that its
    # stderr is being captured to a file. NO_COLOR is the widely-supported
    # convention (no-color.org); TERM=dumb is a defensive fallback for
    # libraries that do not honor NO_COLOR.
    execution_env = ""
    if provider_env is not None:
        if set(provider_env) != {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            raise ValueError("Only the temporary OpenAI proxy environment is supported")
        execution_env = (
            "env -i HOME=/root PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin NO_COLOR=1 TERM=dumb "
            + " ".join(shlex.quote(f"{k}={v}") for k, v in provider_env.items())
            + " "
        )
    inner = (
        f"export PATH=/root/.local/bin:$PATH && "
        f"export NO_COLOR=1 && "
        f"export TERM=dumb && "
        f"cd /workspace && "
        f"{execution_env}amplifier run --bundle {shlex.quote(bundle_name)} "
        f"--output-format json-trace {shlex.quote(prompt)}"
    )
    args = ["exec", "--timeout", str(timeout_s), instance_id, "--", "bash", "-lc", inner]
    t0 = time.time()
    result = _run_dtu(args, timeout=timeout_s, **({"clean_env": True} if provider_env is not None else {}))
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
    # stdout is the json-trace payload (parsed downstream), kept as-is.
    # stderr is the streaming-UI hook output and gets archived as a human-
    # readable log; strip ANSI here so stderr.log is plain text regardless of
    # whether the inner amplifier process honored NO_COLOR/TERM=dumb.
    return (
        int(outer.get("exit_code", -1)),
        outer.get("stdout", ""),
        _strip_ansi(outer.get("stderr", "")),
        elapsed,
    )


def _file_pull_session(
    instance_id: str, dest_dir: Path, *, source_path: str = "/root/.amplifier/projects/"
) -> None:
    """Pull /root/.amplifier/projects/ from the DTU into dest_dir/session/."""
    dest = dest_dir / "session"
    dest.mkdir(parents=True, exist_ok=True)
    args = ["file-pull", "-r", instance_id, source_path, str(dest)]
    result = _run_dtu(args, timeout=300)
    if result.returncode != 0:
        raise RuntimeError("SESSION_PULL_FAILED")


def _verify_workspace(
    instance_id: str, *, workspace_path: str = "/workspace"
) -> tuple[int | None, str, float]:
    """Run pytest inside /workspace AFTER the agent completes.

    The harness records this as a separate signal from amplifier's own exit
    code. Catches "agent claimed pass / amplifier exited 0 but the task was
    not actually solved" — refactor regressions, spec-impl gaps, fix that
    relaxed a test, etc.

    Returns:
        (exit_code, summary, elapsed_seconds)

        exit_code semantics:
          None  - pytest collected no tests; verification is skipped
          0     - pytest passed
          5     - pytest collected no tests; treat as skipped (returned as None)
          other - pytest failed (1) or errored (2/3/4)
    """
    t0 = time.time()

    # Capture pytest's own exit status, not tail's. Running pytest directly
    # also distinguishes no tests (5) from discovery/import/transport failures.
    pytest_cmd = (
        f"cd -- {shlex.quote(workspace_path)} || exit 125; "
        # uv tool installs expose pytest on PATH without installing it into
        # system python3. Prefer the module when available, never interpret
        # a missing verifier's exit 1 as a failed subject test.
        "if python3 -c 'import pytest' >/dev/null 2>&1; then python3 -m pytest --tb=line -q; "
        "elif command -v pytest >/dev/null 2>&1; then pytest --tb=line -q; "
        "else exit 126; fi 2>&1 | tail -40; "
        'pytest_exit=${PIPESTATUS[0]}; exit "$pytest_exit"'
    )
    args = ["exec", "--timeout", "180", instance_id, "--", "bash", "-c", pytest_cmd]
    result = _run_dtu(args, timeout=180)
    elapsed = time.time() - t0
    if result.returncode != 0:
        return -1, f"verify: exec wrapper failed (rc={result.returncode})", elapsed
    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError:
        return -1, "verify: exec envelope is not JSON", elapsed
    if (
        type(outer) is not dict or type(outer.get("exit_code")) is not int
        or type(outer.get("stdout")) is not str or type(outer.get("stderr")) is not str
    ):
        return -1, "verify: invalid exec envelope", elapsed
    pytest_exit = outer["exit_code"]
    summary = (outer["stdout"] + outer["stderr"])[-2000:]
    if pytest_exit == 5:
        # No tests collected — treat as skip
        return None, "verify: skipped (pytest collected 0 tests)\n" + summary, elapsed
    return pytest_exit, summary, elapsed


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


def _proxy_inputs(spec: RunSpec, rendered: RenderedProfile) -> tuple[str, str]:
    """Resolve host references without storing values in specs, profiles or results."""
    config = spec.config.credential_proxy
    key = os.environ.get(config.upstream_key_env, "")
    base = os.environ.get(config.upstream_base_url_env, "")
    if not key or not base:
        raise ValueError("PROXY_HOST_ENV_MISSING")
    # Reuse the existing renderer's URL/credential validation before launching
    # anything; never persist its credential-bearing return here.
    render_nginx_config(base, key, "FAKE_VALIDATION_ONLY_TOKEN_0123456789")
    texts = [rendered.yaml_text, *rendered.launch_vars.values(), *spec.bundle.__dict__.values()]
    if spec.config.settings_overlay is not None:
        texts.append(spec.config.settings_overlay.read_text())
    if any(isinstance(text, str) and key in text for text in texts):
        raise ValueError("PROXY_UPSTREAM_KEY_IN_SOLVER_INPUT")
    return base, key


@contextmanager
def _proxy_environment(run_dir: Path, spec: RunSpec, inputs: tuple[str, str], result: RunResult):
    """Own the existing proxy around execution, with a private resource journal."""
    private_dir = run_dir / "credential-proxy"
    private_dir.mkdir(mode=0o700)
    fd = os.open(
        run_dir / "proxy-resources.jsonl",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    state = {"cleanup": "not_started", "execution": "not_started"}
    result.extras["credential_proxy"] = state
    with os.fdopen(fd, "w") as journal:
        def record(identifier: str, status: str) -> None:
            if re.fullmatch(r"[a-f0-9]{64}", identifier) is None or status not in ("active", "reaped"):
                raise ValueError("PROXY_RESOURCE_INVALID")
            journal.write(json.dumps({"id": identifier, "status": status}) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        state["cleanup"] = "unverified"
        execution_error = None
        with credential_proxy(
            private_dir, inputs[0], inputs[1], spec.config.credential_proxy.bind_ipv4, record
        ) as (base_url, token):
            state["execution"] = "started"
            try:
                yield {"OPENAI_API_KEY": token, "OPENAI_BASE_URL": base_url}
            except BaseException as exc:
                state["execution"] = "raised"
                # Finish the proxy's normal teardown first so an execution
                # failure does not prevent recording confirmed cleanup.
                execution_error = exc
            else:
                state["execution"] = "returned"
        state["cleanup"] = "confirmed"
        if execution_error is not None:
            raise execution_error


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
    # Proxy runs are single-use. Existing directories (including symlinks) are
    # never reused, and all captures/recovery files sit behind this private leaf.
    isolated = spec.config.credential_proxy is not None
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=not isolated)
    result = RunResult(run_id=spec.run_id, started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    started_wall = time.time()

    rendered: RenderedProfile | None = None
    instance_id: str | None = None

    try:
        # 1. Render and write profile
        rendered = render_profile(spec, gitea_url=gitea.url, gitea_token=gitea.token)
        proxy_inputs = _proxy_inputs(spec, rendered) if isolated else None
        profile_path = write_profile(spec, rendered, run_dir)
        result.profile_path = profile_path

        # 2. Launch
        instance_id, elapsed = _launch_dtu(
            profile_path, rendered.launch_vars, timeout_s=spec.config.launch_timeout_s,
            **({"clean_env": True} if isolated else {}),
        )
        result.dtu_instance_id = instance_id
        result.launch_seconds = elapsed

        # 3. Wait readiness
        result.readiness_seconds = _wait_readiness(instance_id)

        # 4. Push workspace fixture (if any)
        if spec.scenario.workspace_path is not None:
            _push_workspace_fixture(instance_id, spec.scenario.workspace_path)

        # 5. Exec amplifier — wrapped in try/finally so steps 6 and 6.1 always
        # run even when exec raises (e.g. subprocess.TimeoutExpired).  The
        # finally block is best-effort: pull/usage failures are logged but do
        # not mask the original exec exception.
        prompt = spec.scenario.prompt_path.read_text()
        try:
            boundary = _proxy_environment(run_dir, spec, proxy_inputs, result) if isolated else nullcontext(None)
            with boundary as provider_env:
                exit_code, stdout, stderr, exec_elapsed = _exec_amplifier(
                    instance_id,
                    bundle_name=spec.bundle.name,
                    prompt=prompt,
                    timeout_s=spec.config.exec_timeout_s,
                    **({"provider_env": provider_env} if isolated else {}),
                )
                # Retain the CLI return even if proxy teardown subsequently
                # fails. Cleanup and CLI execution are separate observations.
                result.exit_code = exit_code
                result.raw_stdout = stdout
                result.raw_stderr = stderr
                result.exec_seconds = exec_elapsed
                result.json_trace = _extract_json_trace(stdout)
        finally:
            # 6. Pull session files — always, even if exec raised or timed out.
            # The DTU may be in a broken state after a timeout; keep this
            # best-effort so a pull failure does not mask the original exec exception.
            try:
                _file_pull_session(instance_id, run_dir)
                result.extras["session_pull"] = "returned"
            except Exception:
                result.extras["session_pull"] = "failed"
                log("  warning: session transfer failed; telemetry coverage is incomplete")

            # 6.1. Aggregate LLM usage from the pulled events.jsonl files.
            # Walks the parent session + every sub-session (delegate, recipes,
            # fork-skills) since each writes its own events.jsonl into the
            # standard ~/.amplifier/projects/<slug>/sessions/<id>/ layout that
            # gets pulled wholesale by step 6.
            try:
                result.usage = collect_usage(run_dir / "session")
                u = result.usage
                if u.request_count:
                    log(
                        f"  ↗ usage: {u.request_count} req across {u.session_count} session(s); "
                        f"{u.billable_tokens_total:,} billable tok ({u.input_tokens_total:,} in / "
                        f"{u.output_tokens_total:,} out); max single call {u.max_call_combined_tokens:,}; "
                        f"llm time {u.llm_time_ms_total / 1000:.1f}s "
                        f"(avg {u.llm_time_ms_avg / 1000:.2f}s, max {u.llm_time_ms_max / 1000:.1f}s)"
                    )
                else:
                    log("  ↗ usage: 0 LLM requests recorded (no events.jsonl or no llm:response events)")
            except Exception as ue:
                log(f"  warning: usage collection failed: {type(ue).__name__}: {ue}")

            # 6.2. Extract the resolved mount plan from the root session's
            # events.jsonl. Requires session.raw=true, which the harness forces
            # on via the force-raw behavior bundle composed through bundle.app
            # in settings.yaml. If extraction returns None, the second
            # session:start event with data.raw was not present — usually
            # because force-raw composition was bypassed or this kernel
            # version doesn't honor session.raw on this code path.
            try:
                mp, sha = extract_mount_plan(run_dir / "session")
                result.mount_plan = mp
                result.mount_plan_sha = sha
                if sha:
                    log(f"  \u2295 mount plan sha={sha[:12]}\u2026")
                else:
                    log("  \u2295 mount plan: not found (session.raw=true may not be active)")
            except Exception as me:
                log(f"  warning: mount plan extraction failed: {type(me).__name__}: {me}")

        # 6a. Post-agent verification — run pytest in /workspace if tests exist.
        # Distinct from amplifier's exit code: catches the case where amplifier
        # ran cleanly to completion but the task wasn't actually solved.
        try:
            v_code, v_summary, v_elapsed = _verify_workspace(instance_id)
            result.verify_exit_code = v_code
            result.verify_summary = v_summary
            result.verify_seconds = v_elapsed
            result.extras["verification"] = (
                "skipped" if v_code is None else
                "passed" if v_code == 0 else
                "failed" if v_code == 1 else "error"
            )
            if v_code is None:
                log("  · verify skipped")
            elif v_code == 0:
                log("  ✓ verify passed (pytest 0)")
            else:
                log(f"  ✗ verify FAILED (pytest rc={v_code})")
        except Exception:
            result.verify_exit_code = -1
            result.verify_summary = "verify: step raised; no pytest verdict"
            result.extras["verification"] = "error"
            log("  warning: verification raised; not a skip or a correctness failure")

    except Exception as e:
        # Upstream validation/callback/subprocess exceptions must not reflect
        # arbitrary text while the host holds a real credential.
        result.error = "CREDENTIAL_PROXY_RUN_FAILED" if isolated else f"{type(e).__name__}: {e}"
        log(f"  ERROR: {result.error}")

    finally:
        # 7. Teardown
        if instance_id is not None:
            failed = result.status != "success"
            keep = spec.config.keep_dtu_on_failure if failed else spec.config.keep_dtu_on_success
            if keep:
                reason = "keep_dtu_on_failure" if failed else "keep_dtu_on_success"
                log(f"  keeping DTU {instance_id} ({reason}=true)")
            else:
                result.teardown_seconds = _destroy_dtu(instance_id)

        result.wall_clock_seconds = time.time() - started_wall
        result.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    return result
