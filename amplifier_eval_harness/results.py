"""Results writers: per-run artifacts + summary CSV/markdown + manifest."""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

import yaml

from .config import RunConfig, RunSpec
from .gitea import GiteaSession
from .runner import RunResult


def _json_default(o: Any) -> Any:
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, dt.datetime):
        return o.isoformat()
    return str(o)


def _spec_to_dict(spec: RunSpec) -> dict[str, Any]:
    return {
        "run_id": spec.run_id,
        "bundle": {
            "name": spec.bundle.name,
            "source": spec.bundle.source,
            "is_local": spec.bundle.is_local,
            "repo_owner": spec.bundle.repo_owner,
            "repo_name": spec.bundle.repo_name,
            "git_ref": spec.bundle.git_ref,
            "local_path": str(spec.bundle.local_path) if spec.bundle.local_path else None,
        },
        "scenario": {
            "id": spec.scenario.id,
            "prompt_path": str(spec.scenario.prompt_path),
            "workspace_path": str(spec.scenario.workspace_path) if spec.scenario.workspace_path else None,
        },
        "run_index": spec.run_index,
    }


def _result_to_dict(result: RunResult) -> dict[str, Any]:
    d = dataclasses.asdict(result)
    # Path → str
    if d.get("profile_path"):
        d["profile_path"] = str(d["profile_path"])
    d["status"] = result.status
    return d


def write_run_artifacts(run_dir: Path, spec: RunSpec, result: RunResult) -> None:
    """Write per-run artifacts inside run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)

    # run-spec.json
    (run_dir / "run-spec.json").write_text(json.dumps(_spec_to_dict(spec), indent=2, default=_json_default))

    # result.json — just the parsed json-trace (or null)
    if result.json_trace is not None:
        (run_dir / "result.json").write_text(json.dumps(result.json_trace, indent=2, default=_json_default))

    # stdout.txt + stderr.log (raw)
    (run_dir / "stdout.txt").write_text(result.raw_stdout)
    (run_dir / "stderr.log").write_text(result.raw_stderr)

    # exit_code (single line, easy to grep)
    (run_dir / "exit_code").write_text(f"{result.exit_code}\n" if result.exit_code is not None else "null\n")

    # wall_clock_s
    (run_dir / "wall_clock_s").write_text(f"{result.wall_clock_seconds:.3f}\n")

    # dtu-info.json
    dtu_info = {
        "instance_id": result.dtu_instance_id,
        "launch_seconds": result.launch_seconds,
        "readiness_seconds": result.readiness_seconds,
        "exec_seconds": result.exec_seconds,
        "teardown_seconds": result.teardown_seconds,
        "wall_clock_seconds": result.wall_clock_seconds,
        "started_at": result.started_at,
        "ended_at": result.ended_at,
        "status": result.status,
        "error": result.error,
    }
    (run_dir / "dtu-info.json").write_text(json.dumps(dtu_info, indent=2, default=_json_default))


def write_manifest(
    output_dir: Path,
    config: RunConfig,
    gitea: GiteaSession,
    started_at: dt.datetime,
    ended_at: dt.datetime,
    results: list[RunResult],
) -> None:
    """Write top-level manifest.json describing this harness invocation."""
    manifest = {
        "config_path": str(config.config_path),
        "output_dir": str(output_dir),
        "amplifier_install_ref": config.amplifier_install_ref,
        "settings_overlay": str(config.settings_overlay) if config.settings_overlay else None,
        "ecosystem_overrides": [
            {
                "repo": e.repo,
                "local_path": str(e.local_path) if e.local_path else None,
                "git_ref": e.git_ref,
            }
            for e in config.ecosystem_overrides
        ],
        "bundles": [
            {
                "name": b.name,
                "source": b.source,
                "is_local": b.is_local,
                "repo_name": b.repo_name,
                "local_path": str(b.local_path) if b.local_path else None,
            }
            for b in config.bundles
        ],
        "scenarios": [{"id": s.id, "prompt_path": str(s.prompt_path)} for s in config.scenarios],
        "runs_per_combo": config.runs_per_combo,
        "gitea": {
            "instance_id": gitea.instance_id,
            "port": gitea.port,
            "url": gitea.url,
        },
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": (ended_at - started_at).total_seconds(),
        "total_runs": len(results),
        "successes": sum(1 for r in results if r.status == "success"),
        "amplifier_errors": sum(1 for r in results if r.status == "amplifier_error"),
        "harness_errors": sum(1 for r in results if r.status == "harness_error"),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=_json_default))


def write_summary_csv(output_dir: Path, specs_results: list[tuple[RunSpec, RunResult]]) -> None:
    """One row per run."""
    path = output_dir / "summary.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "run_id",
                "bundle",
                "scenario",
                "run_index",
                "status",
                "exit_code",
                "wall_clock_s",
                "amplifier_duration_ms",
                "tool_calls",
                "agents_invoked",
                "model",
                "error",
            ]
        )
        for spec, result in specs_results:
            jt = result.json_trace or {}
            md = jt.get("metadata") or {}
            w.writerow(
                [
                    result.run_id,
                    spec.bundle.name,
                    spec.scenario.id,
                    spec.run_index,
                    result.status,
                    result.exit_code if result.exit_code is not None else "",
                    f"{result.wall_clock_seconds:.3f}",
                    md.get("duration_ms", ""),
                    md.get("total_tool_calls", ""),
                    md.get("total_agents_invoked", ""),
                    jt.get("model", ""),
                    (result.error or jt.get("error") or "").replace("\n", " | ")[:200],
                ]
            )


def write_summary_md(output_dir: Path, specs_results: list[tuple[RunSpec, RunResult]]) -> None:
    """Human-readable summary."""
    lines: list[str] = []
    lines.append("# Eval Run Summary")
    lines.append("")
    lines.append(f"- Output dir: `{output_dir}`")
    lines.append(f"- Total runs: {len(specs_results)}")
    counts: dict[str, int] = {}
    for _, r in specs_results:
        counts[r.status] = counts.get(r.status, 0) + 1
    for status, n in sorted(counts.items()):
        lines.append(f"- {status}: {n}")
    lines.append("")

    lines.append("## Per-run results")
    lines.append("")
    lines.append("| Bundle | Scenario | Run | Status | Exit | Wall (s) | Amp dur (ms) | Tools | Agents | Notes |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for spec, result in specs_results:
        jt = result.json_trace or {}
        md = jt.get("metadata") or {}
        notes = (result.error or jt.get("error") or "").replace("\n", " ").replace("|", "\\|")[:80]
        lines.append(
            f"| {spec.bundle.name} | {spec.scenario.id} | {spec.run_index} | "
            f"{result.status} | {result.exit_code if result.exit_code is not None else '—'} | "
            f"{result.wall_clock_seconds:.1f} | {md.get('duration_ms', '—')} | "
            f"{md.get('total_tool_calls', '—')} | {md.get('total_agents_invoked', '—')} | {notes} |"
        )

    (output_dir / "summary.md").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Re-export yaml so callers can dump rendered profiles for debugging if needed
# ---------------------------------------------------------------------------

__all__ = [
    "write_run_artifacts",
    "write_manifest",
    "write_summary_csv",
    "write_summary_md",
    "yaml",
]
