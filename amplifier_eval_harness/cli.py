"""amplifier-eval-harness CLI entry point."""

from __future__ import annotations

import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

from ._log import log
from .config import RunConfig, expand_matrix, load_config, validate_paths
from .gitea import GiteaSession, ensure_gitea, populate_repo
from .profile import ALWAYS_MIRROR_REPOS
from .results import write_manifest, write_run_artifacts, write_summary_csv, write_summary_md
from .runner import run_one

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_repos_to_populate(config: RunConfig) -> list[tuple[str, str, Path | None]]:
    """Return [(repo_owner, repo_name, local_path_or_None), ...] for every repo
    that needs to live in Gitea before runs start. Deduplicated, stable order."""
    seen: set[str] = set()
    out: list[tuple[str, str, Path | None]] = []

    def add(owner: str, name: str, local_path: Path | None) -> None:
        if name in seen:
            return
        seen.add(name)
        out.append((owner, name, local_path))

    # Ecosystem overrides go FIRST — they are explicit user-configured overrides
    # and must win over any incidental reference to the same repo via a bundle's
    # source URL. (Otherwise a bundle that lives in amplifier-foundation will
    # add the repo with local_path=None, the dedup hits, and the snapshot-push
    # request from the override is silently dropped.)
    for e in config.ecosystem_overrides:
        if "/" in e.repo:
            owner, name = e.repo.split("/", 1)
        else:
            owner, name = "microsoft", e.repo
        add(owner, name, e.local_path)

    # Always-mirror entry points (so the install inside DTU resolves to our mirror)
    for owner, name in ALWAYS_MIRROR_REPOS:
        add(owner, name, None)

    # Bundles last — any bundle whose source repo is already covered by an
    # ecosystem override gets deduplicated (the override entry already in the
    # list carries the right local_path).
    for b in config.bundles:
        add(b.repo_owner, b.repo_name, b.local_path)

    return out


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@click.group()
@click.version_option()
def main() -> None:
    """Run scenarios through amplifier-app-cli inside DTU containers."""


@main.command()
@click.option(
    "--config",
    "-c",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the YAML config file.",
)
@click.option(
    "--dry-run", is_flag=True, help="Validate the config and print the expanded matrix without launching anything."
)
@click.option(
    "--parallelism", "parallelism_override", type=int, default=None, help="Override parallelism from the config (>= 1)."
)
def run(config_path: Path, dry_run: bool, parallelism_override: int | None) -> None:
    """Execute the matrix of (bundle x scenario x run) combos defined by the config."""
    log(f"Loading config: {config_path}")
    config = load_config(config_path)

    if parallelism_override is not None:
        if parallelism_override < 1:
            click.echo("--parallelism must be >= 1", err=True)
            sys.exit(2)
        config.parallelism = parallelism_override

    issues = validate_paths(config)
    if issues:
        click.echo("Config validation failed:", err=True)
        for issue in issues:
            click.echo(f"  - {issue}", err=True)
        sys.exit(2)

    specs = expand_matrix(config)

    log("=" * 70)
    log(f"Resolved config:  {config.config_path}")
    log(f"Output dir:       {config.output_dir}")
    log(f"Profile template: {config.profile_template}")
    log(f"Settings overlay: {config.settings_overlay or '(none)'}")
    log(f"Parallelism:      {config.parallelism}")
    log(f"Bundles:          {len(config.bundles)}")
    for b in config.bundles:
        local_marker = " (LOCAL)" if b.is_local else ""
        sub_marker = f" [subdir={b.subdirectory}]" if b.subdirectory else ""
        log(f"  - {b.name}: {b.source}{local_marker}{sub_marker}")
    log(f"Scenarios:        {len(config.scenarios)}")
    for s in config.scenarios:
        log(f"  - {s.id}")
    log(f"Runs per combo:   {config.runs_per_combo}")
    log(f"Total runs:       {len(specs)}")
    log("=" * 70)

    if dry_run:
        log("--dry-run: not launching anything.")
        for spec in specs:
            log(f"  would run: {spec.run_id}")
        sys.exit(0)

    config.output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Gitea session
    log("Ensuring Gitea instance...")
    gitea: GiteaSession = ensure_gitea()
    log(f"Gitea ready: {gitea.url} (instance {gitea.instance_id})")

    # Step 2: Populate every repo we care about (sequential — Gitea is shared state)
    log("Populating Gitea with repos...")
    for owner, name, local_path in _resolve_repos_to_populate(config):
        marker = " (LOCAL snapshot)" if local_path else ""
        log(f"  · {owner}/{name}{marker}")
        populate_repo(gitea, repo_owner=owner, repo_name=name, local_path=local_path)

    # Step 3: Run the matrix
    started_at = dt.datetime.now()
    runs_dir = config.output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    specs_results: list[tuple] = []  # list[tuple[RunSpec, RunResult]]

    if config.parallelism == 1:
        # Sequential path — simpler, used for inner-dev-loop.
        for i, spec in enumerate(specs, 1):
            log(f"\n[{i}/{len(specs)}] {spec.run_id}")
            run_dir = runs_dir / spec.run_id
            result = run_one(spec, gitea, run_dir)
            write_run_artifacts(run_dir, spec, result)
            specs_results.append((spec, result))
            log(f"  → status={result.status} exit={result.exit_code} wall={result.wall_clock_seconds:.1f}s")
            if result.error:
                log(f"  → error: {result.error}")
    else:
        # Parallel path — bounded by parallelism.
        log(f"\nRunning {len(specs)} combos with up to {config.parallelism} concurrent DTUs...")
        with ThreadPoolExecutor(max_workers=config.parallelism) as ex:
            futures = {ex.submit(_run_and_persist, spec, gitea, runs_dir / spec.run_id): spec for spec in specs}
            for i, fut in enumerate(as_completed(futures), 1):
                spec = futures[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    # Should be rare — run_one captures most errors internally.
                    log(f"[{i}/{len(specs)}] {spec.run_id} → uncaught: {type(e).__name__}: {e}")
                    raise
                specs_results.append((spec, result))
                log(
                    f"[{i}/{len(specs)}] DONE {spec.run_id} → status={result.status} "
                    f"exit={result.exit_code} wall={result.wall_clock_seconds:.1f}s"
                )

    ended_at = dt.datetime.now()

    # Step 4: Summaries
    write_manifest(
        config.output_dir,
        config,
        gitea,
        started_at,
        ended_at,
        [r for _, r in specs_results],
    )
    write_summary_csv(config.output_dir, specs_results)
    write_summary_md(config.output_dir, specs_results)

    log("=" * 70)
    log(f"Done. Output: {config.output_dir}")
    log("  manifest.json | summary.csv | summary.md")
    log("=" * 70)

    harness_errors = sum(1 for _, r in specs_results if r.status == "harness_error")
    sys.exit(1 if harness_errors > 0 else 0)


def _run_and_persist(spec, gitea, run_dir):
    """Worker-thread entry: run + persist artifacts in one call so partial results
    are written even if the orchestrator exits before all futures complete."""
    result = run_one(spec, gitea, run_dir)
    write_run_artifacts(run_dir, spec, result)
    return result


@main.command("validate")
@click.option(
    "--config",
    "-c",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def validate_cmd(config_path: Path) -> None:
    """Validate a config file and print the expanded matrix."""
    config = load_config(config_path)
    issues = validate_paths(config)
    specs = expand_matrix(config)
    if issues:
        click.echo("Issues:", err=True)
        for issue in issues:
            click.echo(f"  - {issue}", err=True)
        sys.exit(2)
    click.echo(
        f"OK. {len(config.bundles)} bundles × {len(config.scenarios)} scenarios × "
        f"{config.runs_per_combo} runs = {len(specs)} total runs (parallelism={config.parallelism})."
    )
    for s in specs:
        click.echo(f"  - {s.run_id}")


@main.command("gitea-status")
def gitea_status_cmd() -> None:
    """Show current Gitea instance state (debugging aid)."""
    try:
        session = ensure_gitea()
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)
    click.echo(
        json.dumps(
            {
                "instance_id": session.instance_id,
                "port": session.port,
                "url": session.url,
                "token_set": bool(session.token),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
