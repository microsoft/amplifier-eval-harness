"""eval-harness CLI entry point."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import click

from .config import expand_matrix, load_config, validate_paths
from .gitea import GiteaSession, ensure_gitea, populate_repo
from .results import write_manifest, write_run_artifacts, write_summary_csv, write_summary_md
from .runner import run_one

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_repos_to_populate(config) -> list[tuple[str, str, Path | None]]:
    """Return [(repo_owner, repo_name, local_path_or_None), ...] for every repo
    that needs to live in Gitea before runs start."""
    out: dict[str, tuple[str, str, Path | None]] = {}

    # Always mirror the amplifier entry-point and CLI repos so the install inside DTU
    # routes through Gitea.
    for repo in ("amplifier", "amplifier-app-cli"):
        out[repo] = ("microsoft", repo, None)

    # Bundles
    for b in config.bundles:
        out[b.repo_name] = (b.repo_owner, b.repo_name, b.local_path)

    # Ecosystem overrides
    for e in config.ecosystem_overrides:
        if "/" in e.repo:
            owner, name = e.repo.split("/", 1)
        else:
            owner, name = "microsoft", e.repo
        out[name] = (owner, name, e.local_path)

    return list(out.values())


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
    "--dry-run",
    is_flag=True,
    help="Validate the config and print the expanded matrix without launching anything.",
)
def run(config_path: Path, dry_run: bool) -> None:
    """Execute the matrix of (bundle x scenario x run) combos defined by the config."""
    click.echo(f"Loading config: {config_path}", err=True)
    config = load_config(config_path)

    issues = validate_paths(config)
    if issues:
        click.echo("Config validation failed:", err=True)
        for issue in issues:
            click.echo(f"  - {issue}", err=True)
        sys.exit(2)

    specs = expand_matrix(config)

    click.echo("=" * 70, err=True)
    click.echo(f"Resolved config:  {config.config_path}", err=True)
    click.echo(f"Output dir:       {config.output_dir}", err=True)
    click.echo(f"Profile template: {config.profile_template}", err=True)
    click.echo(f"Bundles:          {len(config.bundles)}", err=True)
    for b in config.bundles:
        click.echo(f"  - {b.name}: {b.source} {'(LOCAL)' if b.is_local else ''}", err=True)
    click.echo(f"Scenarios:        {len(config.scenarios)}", err=True)
    for s in config.scenarios:
        click.echo(f"  - {s.id}", err=True)
    click.echo(f"Runs per combo:   {config.runs_per_combo}", err=True)
    click.echo(f"Total runs:       {len(specs)}", err=True)
    click.echo("=" * 70, err=True)

    if dry_run:
        click.echo("--dry-run: not launching anything.", err=True)
        for spec in specs:
            click.echo(f"  would run: {spec.run_id}", err=True)
        sys.exit(0)

    config.output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Gitea session
    click.echo("Ensuring Gitea instance...", err=True)
    gitea: GiteaSession = ensure_gitea()
    click.echo(f"Gitea ready: {gitea.url} (instance {gitea.instance_id})", err=True)

    # Step 2: Populate every repo we care about
    click.echo("Populating Gitea with repos...", err=True)
    for owner, name, local_path in _resolve_repos_to_populate(config):
        click.echo(f"  · {owner}/{name}{' (LOCAL snapshot)' if local_path else ''}", err=True)
        populate_repo(gitea, repo_owner=owner, repo_name=name, local_path=local_path)

    # Step 3: Run the matrix
    started_at = dt.datetime.now()
    specs_results: list[tuple] = []  # type: ignore[var-annotated]
    runs_dir = config.output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    for i, spec in enumerate(specs, 1):
        click.echo(f"\n[{i}/{len(specs)}] {spec.run_id}", err=True)
        run_dir = runs_dir / spec.run_id
        result = run_one(spec, gitea, run_dir)
        write_run_artifacts(run_dir, spec, result)
        specs_results.append((spec, result))
        click.echo(
            f"  → status={result.status} exit={result.exit_code} wall={result.wall_clock_seconds:.1f}s",
            err=True,
        )
        if result.error:
            click.echo(f"  → error: {result.error}", err=True)

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

    click.echo("=" * 70, err=True)
    click.echo(f"Done. Output: {config.output_dir}", err=True)
    click.echo("  manifest.json | summary.csv | summary.md", err=True)
    click.echo("=" * 70, err=True)

    # Exit nonzero if any harness errors
    harness_errors = sum(1 for _, r in specs_results if r.status == "harness_error")
    sys.exit(1 if harness_errors > 0 else 0)


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
        f"{config.runs_per_combo} runs = {len(specs)} total runs."
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
