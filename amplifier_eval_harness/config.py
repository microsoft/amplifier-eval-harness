"""Config schema, loader, validator, matrix expander.

The harness is driven entirely by a YAML config. This module defines the schema
as dataclasses, loads + validates a config file, and expands the (bundle, scenario,
run_index) cartesian product into a flat list of RunSpec.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Source-URL parsers
# ---------------------------------------------------------------------------

# git+https://github.com/<owner>/<repo>[@<ref>][#subdirectory=<path>]
_GIT_GITHUB_RE = re.compile(
    r"^git\+https://github\.com/(?P<owner>[^/]+)/(?P<name>[^@/#]+?)"
    r"(?:@(?P<ref>[^#]+))?"
    r"(?:#subdirectory=(?P<subdir>.+))?$"
)

# file://<local-path>[#subdirectory=<path>]
_FILE_RE = re.compile(r"^file://(?P<path>[^#]+)(?:#subdirectory=(?P<subdir>.+))?$")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BundleSpec:
    """A bundle to test. Source is upstream git or a local working tree, with optional subdirectory."""

    name: str  # used in `amplifier bundle add ... --name <name>` and `amplifier bundle use <name>`
    source: str  # "git+https://..." | "file://..."

    # Derived in __post_init__:
    repo_owner: str = ""
    repo_name: str = ""
    git_ref: str = "main"
    subdirectory: str | None = None  # path within the repo (e.g. "bundles/amplifier-dev.yaml")
    local_path: Path | None = None
    is_local: bool = False

    def __post_init__(self) -> None:
        m = _GIT_GITHUB_RE.match(self.source)
        if m:
            self.repo_owner = m.group("owner")
            self.repo_name = m.group("name").rstrip("/").removesuffix(".git")
            self.git_ref = m.group("ref") or "main"
            self.subdirectory = m.group("subdir")
            self.is_local = False
            return
        m = _FILE_RE.match(self.source)
        if m:
            path_str = m.group("path")
            self.local_path = Path(path_str).expanduser().resolve()
            # Use the directory name as the repo name (consistent with `microsoft/<dir-name>`)
            self.repo_name = self.local_path.name
            self.repo_owner = "microsoft"  # synthetic — Gitea always uses admin/<repo-name>
            self.git_ref = "main"
            self.subdirectory = m.group("subdir")
            self.is_local = True
            return
        raise ValueError(
            f"BundleSpec.source must be 'git+https://github.com/<owner>/<repo>[@ref][#subdirectory=<path>]' "
            f"or 'file://<local-path>[#subdirectory=<path>]', got: {self.source!r}"
        )

    def install_url(self) -> str:
        """Reconstruct the install URL passed to `amplifier bundle add ...`.

        Inside a DTU we always install via git+https (mitmproxy redirects it to Gitea).
        Local sources are routed via Gitea after the snapshot push, so the install
        URL is always the github form, regardless of `is_local`.
        """
        url = f"git+https://github.com/{self.repo_owner}/{self.repo_name}@{self.git_ref}"
        if self.subdirectory:
            url = f"{url}#subdirectory={self.subdirectory}"
        return url


@dataclass
class ScenarioSpec:
    """A scenario: a prompt, optionally with a workspace fixture seeded into /workspace."""

    id: str  # short identifier, used in run dir names
    prompt_path: Path  # markdown/text file containing the prompt
    workspace_path: Path | None = None  # optional dir of fixture files for /workspace inside DTU


@dataclass
class EcosystemOverride:
    """An ecosystem-level repo override applied to ALL runs in this config.

    Examples: pin amplifier-foundation to a local checkout, point amplifier-core
    at a specific branch, etc. These apply on top of the per-bundle source.
    """

    repo: str  # "microsoft/<repo-name>"
    local_path: Path | None = None  # if set, snapshot push from this local working tree
    git_ref: str | None = None  # if set (and no local_path), mirror this ref from upstream


@dataclass
class RunConfig:
    """The fully-resolved config for one harness invocation."""

    config_path: Path  # absolute path to the YAML this was loaded from
    output_dir: Path  # where eval-results/ goes for this run
    profile_template: Path  # path to the parameterized DTU profile template
    parallelism: int = 1  # 1 = sequential; N > 1 = up to N concurrent DTUs
    amplifier_install_ref: str = "main"
    launch_timeout_s: int = 600
    exec_timeout_s: int = 900
    keep_dtu_on_failure: bool = True
    keep_dtu_on_success: bool = False
    settings_overlay: Path | None = None
    # Pin to a specific Gitea instance instead of greedily reusing whichever
    # one `amplifier-gitea list` returns first. Required when multiple
    # workspaces share a host so they don't stomp on each other's instance.
    # Resolution order: env var EVAL_HARNESS_GITEA_INSTANCE > YAML
    # `gitea_instance_id` > unset (falls back to greedy reuse / create-new).
    gitea_instance_id: str | None = None
    ecosystem_overrides: list[EcosystemOverride] = field(default_factory=list)
    bundles: list[BundleSpec] = field(default_factory=list)
    scenarios: list[ScenarioSpec] = field(default_factory=list)
    runs_per_combo: int = 1


@dataclass
class RunSpec:
    """A single run in the matrix: one bundle × one scenario × one run_index."""

    bundle: BundleSpec
    scenario: ScenarioSpec
    run_index: int  # 1-based for human-readability
    config: RunConfig

    @property
    def run_id(self) -> str:
        """Short identifier for this run, used in directory names."""
        return f"{self.bundle.name}__{self.scenario.id}__r{self.run_index}"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _resolve(path_str: str, base: Path) -> Path:
    """Resolve a path relative to the config file's directory if not absolute."""
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (base / p).resolve()


def load_config(config_path: str | Path) -> RunConfig:
    """Load and validate a YAML config, returning a fully-resolved RunConfig."""
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    base = config_path.parent

    # Output dir: default to <project>/eval-results/<config-stem>-<timestamp>/
    #
    # The README documents output as "eval-results/<config-stem>-<timestamp>/"
    # rooted at the project (one level above configs/), and the rest of this
    # loader uses base.parent for the same project-root anchor (profile_template
    # and scenarios both default to base.parent / ...). Earlier versions used
    # `base / "eval-results"`, which placed results under configs/eval-results/.
    # The .gitignore intentionally matches `eval-results/` at any depth, so
    # both layouts were ignored, but only the project-root layout matched the
    # README and the rest of the path conventions.
    if "output_dir" in raw:
        output_dir = _resolve(raw["output_dir"], base)
    else:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = (base.parent / "eval-results" / f"{config_path.stem}-{ts}").resolve()

    # Profile template: default to <package>/profiles/eval-base.yaml.tmpl,
    # but a config can override.
    if "profile" in raw:
        profile_template = _resolve(raw["profile"], base)
    else:
        candidate = base.parent / "profiles" / "eval-base.yaml.tmpl"
        if candidate.is_file():
            profile_template = candidate.resolve()
        else:
            pkg_root = Path(__file__).parent
            profile_template = (pkg_root / "_data" / "profiles" / "eval-base.yaml.tmpl").resolve()

    settings_overlay = _resolve(raw["settings_overlay"], base) if raw.get("settings_overlay") else None

    # Gitea instance pinning. Env var wins so per-invocation overrides work
    # without editing the config (handy when the same config gets shared
    # across machines / workspaces).
    gitea_instance_id: str | None = os.environ.get("EVAL_HARNESS_GITEA_INSTANCE") or raw.get("gitea_instance_id")
    if gitea_instance_id is not None:
        gitea_instance_id = str(gitea_instance_id).strip() or None

    # Ecosystem overrides
    eco: list[EcosystemOverride] = []
    for entry in raw.get("ecosystem_overrides") or []:
        local_path = _resolve(entry["local_path"], base) if entry.get("local_path") else None
        eco.append(EcosystemOverride(repo=entry["repo"], local_path=local_path, git_ref=entry.get("git_ref")))

    # Bundles
    bundles: list[BundleSpec] = []
    for entry in raw.get("bundles") or []:
        if "name" not in entry or "source" not in entry:
            raise ValueError(f"Each bundle must have 'name' and 'source'. Got: {entry!r}")
        # If source is a relative file:// path, resolve against config dir.
        source = entry["source"]
        m = _FILE_RE.match(source)
        if m and not Path(m.group("path")).is_absolute():
            resolved_local = _resolve(m.group("path"), base)
            source = f"file://{resolved_local}"
            if m.group("subdir"):
                source = f"{source}#subdirectory={m.group('subdir')}"
        bundles.append(BundleSpec(name=entry["name"], source=source))

    if not bundles:
        raise ValueError("Config must define at least one bundle.")

    # Scenarios
    scenarios: list[ScenarioSpec] = []
    for entry in raw.get("scenarios") or []:
        if isinstance(entry, str):
            sid = entry
            sdir = base.parent / "scenarios" / sid
            scenarios.append(
                ScenarioSpec(
                    id=sid,
                    prompt_path=(sdir / "prompt.md").resolve(),
                    workspace_path=(sdir / "workspace").resolve() if (sdir / "workspace").is_dir() else None,
                )
            )
            continue
        if "id" not in entry:
            raise ValueError(f"Each scenario must have 'id'. Got: {entry!r}")
        sid = entry["id"]
        if "prompt_path" in entry:
            prompt_path = _resolve(entry["prompt_path"], base)
        else:
            prompt_path = (base.parent / "scenarios" / sid / "prompt.md").resolve()
        if "workspace_path" in entry:
            workspace_path = _resolve(entry["workspace_path"], base)
        else:
            ws = (base.parent / "scenarios" / sid / "workspace").resolve()
            workspace_path = ws if ws.is_dir() else None
        scenarios.append(ScenarioSpec(id=sid, prompt_path=prompt_path, workspace_path=workspace_path))

    if not scenarios:
        raise ValueError("Config must define at least one scenario.")

    parallelism = int(raw.get("parallelism", 1))
    if parallelism < 1:
        raise ValueError(f"parallelism must be >= 1, got {parallelism!r}")

    return RunConfig(
        config_path=config_path,
        output_dir=output_dir,
        profile_template=profile_template,
        parallelism=parallelism,
        amplifier_install_ref=str(raw.get("amplifier_install_ref", "main")),
        launch_timeout_s=int(raw.get("launch_timeout_s", 600)),
        exec_timeout_s=int(raw.get("exec_timeout_s", 900)),
        keep_dtu_on_failure=bool(raw.get("keep_dtu_on_failure", True)),
        keep_dtu_on_success=bool(raw.get("keep_dtu_on_success", False)),
        settings_overlay=settings_overlay,
        gitea_instance_id=gitea_instance_id,
        ecosystem_overrides=eco,
        bundles=bundles,
        scenarios=scenarios,
        runs_per_combo=int(raw.get("runs_per_combo", 1)),
    )


def expand_matrix(config: RunConfig) -> list[RunSpec]:
    """Expand bundles × scenarios × runs_per_combo into a flat list of RunSpec."""
    result: list[RunSpec] = []
    for bundle in config.bundles:
        for scenario in config.scenarios:
            for r in range(1, config.runs_per_combo + 1):
                result.append(RunSpec(bundle=bundle, scenario=scenario, run_index=r, config=config))
    return result


def validate_paths(config: RunConfig) -> list[str]:
    """Return a list of human-readable issues with the config (empty list = OK)."""
    issues: list[str] = []
    if not config.profile_template.is_file():
        issues.append(f"Profile template not found: {config.profile_template}")
    if config.settings_overlay and not config.settings_overlay.is_file():
        issues.append(f"settings_overlay file not found: {config.settings_overlay}")
    for bundle in config.bundles:
        if bundle.is_local and (bundle.local_path is None or not bundle.local_path.is_dir()):
            issues.append(f"Bundle '{bundle.name}' local path not a directory: {bundle.local_path}")
    for scenario in config.scenarios:
        if not scenario.prompt_path.is_file():
            issues.append(f"Scenario '{scenario.id}' prompt not found: {scenario.prompt_path}")
        if scenario.workspace_path is not None and not scenario.workspace_path.is_dir():
            issues.append(f"Scenario '{scenario.id}' workspace not a directory: {scenario.workspace_path}")
    for eco in config.ecosystem_overrides:
        if eco.local_path is not None and not eco.local_path.is_dir():
            issues.append(f"ecosystem_override '{eco.repo}' local_path not a directory: {eco.local_path}")
    return issues
