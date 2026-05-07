"""Profile rendering: take a parameterized template + a RunSpec, produce a concrete profile YAML.

The DTU profile is mostly fixed (one base image, one mitmproxy setup, same provisioning shape).
What varies per-run is:
  - the bundle URL/name installed inside the container
  - the set of url_rewrites rules (one per repo we want to redirect to Gitea)
  - whether pypi_overrides is present (only when amplifier-core is locally overridden)
  - whether settings_overlay deep-merge is included

The template uses ${VAR} placeholders for things resolved at LAUNCH time
(GITEA_URL, GITEA_TOKEN, BUNDLE_REPO, BUNDLE_NAME, SCENARIO_ID). Things resolved
at RENDER time (the dynamic url_rewrites rules, optional pypi_overrides, optional
settings overlay block) are spliced in here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import EcosystemOverride, RunSpec


@dataclass
class RenderedProfile:
    yaml_text: str
    launch_vars: dict[str, str]


def _build_url_rewrites_rules(spec: RunSpec) -> str:
    """Produce the YAML lines under `url_rewrites.rules:` for this run.

    Always includes a rule for the bundle. Adds rules for every ecosystem_override
    EXCEPT amplifier-core (that goes through pypi_overrides).
    """
    lines: list[str] = []

    # Bundle rule
    bundle_repo = spec.bundle.repo_name
    lines.append(f"    - match: github.com/microsoft/{bundle_repo}")
    lines.append(f"      target: ${{GITEA_URL}}/admin/{bundle_repo}")

    # Ecosystem overrides (skip amplifier-core)
    for eco in spec.config.ecosystem_overrides:
        owner, name = eco.repo.split("/", 1) if "/" in eco.repo else ("microsoft", eco.repo)
        if name == "amplifier-core":
            continue
        lines.append(f"    - match: github.com/{owner}/{name}")
        lines.append(f"      target: ${{GITEA_URL}}/admin/{name}")

    # Always also redirect the amplifier entry-point and amplifier-app-cli through Gitea
    # so the install-from-github inside the DTU goes through our mirror. This guarantees
    # we're testing the version we mirrored, not whatever HEAD upstream has.
    for repo in ("amplifier", "amplifier-app-cli"):
        lines.append(f"    - match: github.com/microsoft/{repo}")
        lines.append(f"      target: ${{GITEA_URL}}/admin/{repo}")

    return "\n".join(lines)


def _build_pypi_overrides_block(eco_overrides: list[EcosystemOverride]) -> str:
    """Produce the `pypi_overrides:` block when amplifier-core is in ecosystem_overrides.

    Returns empty string if amplifier-core is NOT being overridden.
    """
    core_override = next(
        (e for e in eco_overrides if e.repo.split("/")[-1] == "amplifier-core"),
        None,
    )
    if core_override is None:
        return ""

    return """\
pypi_overrides:
  packages:
    - name: amplifier-core
      wheel_from_git:
        repo: ${GITEA_URL}/admin/amplifier-core.git
        ref: main
        username: admin
        token_var: GITEA_TOKEN
        build_cmd: uv run --with maturin maturin build --release
        wheel_glob: target/wheels/amplifier_core-*.whl
"""


def _build_settings_overlay_block(spec: RunSpec) -> tuple[str, str]:
    """Return (provision_files_block, setup_cmd_block) for settings.yaml deep-merge.

    Empty strings if no overlay is configured.
    """
    if spec.config.settings_overlay is None:
        return "", ""

    overlay_path = spec.config.settings_overlay

    files_block = f"""\
  files:
    - src: {overlay_path}
      dest: /tmp/settings_overlay.yaml
      mode: "0644"
"""

    setup_cmd = """\
    - |
      python3 - << 'PYEOF'
      import yaml, json
      from pathlib import Path
      def deep_merge(base, overlay):
          if not isinstance(base, dict) or not isinstance(overlay, dict):
              return overlay
          result = dict(base)
          for k, v in overlay.items():
              if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                  result[k] = deep_merge(result[k], v)
              elif k in result and isinstance(result[k], list) and isinstance(v, list):
                  seen = set(); merged = []
                  for item in result[k] + v:
                      key = json.dumps(item, sort_keys=True, default=str) if isinstance(item, (dict, list)) else (type(item).__name__, item)
                      key = str(key)
                      if key not in seen:
                          seen.add(key); merged.append(item)
                  result[k] = merged
              else:
                  result[k] = v
          return result
      base_path = Path("/root/.amplifier/settings.yaml")
      base = yaml.safe_load(base_path.read_text()) if base_path.exists() else {}
      overlay = yaml.safe_load(Path("/tmp/settings_overlay.yaml").read_text())
      merged = deep_merge(base or {}, overlay or {})
      base_path.parent.mkdir(parents=True, exist_ok=True)
      base_path.write_text(yaml.safe_dump(merged, default_flow_style=False, sort_keys=False))
      print("settings_overlay merged into /root/.amplifier/settings.yaml")
      PYEOF
"""

    return files_block, setup_cmd


def render_profile(spec: RunSpec, gitea_url: str, gitea_token: str) -> RenderedProfile:
    """Render the profile template for this RunSpec, returning YAML text + launch vars."""
    template_text = spec.config.profile_template.read_text()

    url_rewrites_rules = _build_url_rewrites_rules(spec)
    pypi_overrides_block = _build_pypi_overrides_block(spec.config.ecosystem_overrides)
    overlay_files_block, overlay_setup_cmd = _build_settings_overlay_block(spec)

    rendered = (
        template_text.replace("{{ URL_REWRITES_RULES }}", url_rewrites_rules)
        .replace("{{ PYPI_OVERRIDES }}", pypi_overrides_block.rstrip())
        .replace("{{ SETTINGS_OVERLAY_FILES }}", overlay_files_block.rstrip())
        .replace("{{ SETTINGS_OVERLAY_SETUP_CMD }}", overlay_setup_cmd.rstrip())
    )

    launch_vars = {
        "GITEA_URL": gitea_url,
        "GITEA_TOKEN": gitea_token,
        "BUNDLE_REPO": spec.bundle.repo_name,
        "BUNDLE_NAME": spec.bundle.name,
        "SCENARIO_ID": spec.scenario.id,
        "AMPLIFIER_INSTALL_REF": spec.config.amplifier_install_ref,
    }

    return RenderedProfile(yaml_text=rendered, launch_vars=launch_vars)


def write_profile(spec: RunSpec, rendered: RenderedProfile, run_dir: Path) -> Path:
    """Write the rendered profile YAML to <run_dir>/profile.yaml and return its path."""
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "profile.yaml"
    out.write_text(rendered.yaml_text)
    return out
