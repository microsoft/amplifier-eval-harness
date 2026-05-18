"""Profile rendering: take a parameterized template + a RunSpec, produce a concrete profile YAML.

The DTU profile is mostly fixed (one base image, one mitmproxy setup, same provisioning shape).
What varies per-run is:
  - the bundle URL/name installed inside the container
  - the set of url_rewrites rules (one per repo we want to redirect to Gitea)
  - whether pypi_overrides is present (only when amplifier-core is locally overridden)
  - whether settings_overlay deep-merge is included
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .config import RunSpec

# ---------------------------------------------------------------------------
# Nested-DTU Gitea host override
# ---------------------------------------------------------------------------

# When the harness runs inside an Incus DTU and spawns eval-sub-DTUs as siblings
# via a forwarded incus socket, the sub-DTUs' "localhost" is their own loopback —
# not the harness DTU's. The hardcoded "http://localhost:<port>" GITEA_URL baked
# into sub-DTU profiles resolves to nowhere, causing `uv tool install` to fail on
# any transitive git+https://github.com/microsoft/... dependency (mitmproxy's
# url_rewrites redirect those to an unreachable Gitea).
#
# Fix: set AMPLIFIER_EVAL_HARNESS_GITEA_HOST=<harness-eth0-ip> and this module
# rewrites the host portion of GITEA_URL in sub-DTU launch vars so sub-DTUs reach
# the harness's Gitea. Local harness Gitea operations (API calls, mirroring, token
# fetches) are NOT affected — they still use GiteaSession.url (localhost).
#
# Example (inside resolve-stack-v2):
#   export AMPLIFIER_EVAL_HARNESS_GITEA_HOST=10.119.176.124
#   amplifier-eval-harness run --config configs/smoke.yaml
#
_ENV_GITEA_HOST = "AMPLIFIER_EVAL_HARNESS_GITEA_HOST"


def _resolve_dtu_gitea_url(gitea_url: str) -> str:
    """Rewrite gitea_url's host for use by eval-sub-DTUs when running inside a nested DTU.

    If AMPLIFIER_EVAL_HARNESS_GITEA_HOST is set and non-empty, the host portion of
    gitea_url is replaced with its value. The scheme and port are preserved. If the
    env var is absent or empty, gitea_url is returned unchanged.

    Only the GITEA_URL passed to sub-DTU launch vars is affected; local harness Gitea
    operations (API calls, mirroring) still use GiteaSession.url (localhost) directly.
    """
    override_host = os.environ.get(_ENV_GITEA_HOST, "").strip()
    if not override_host:
        return gitea_url
    parsed = urlparse(gitea_url)
    # netloc is "host:port" or just "host"; replace only the host, keep the port.
    if ":" in parsed.netloc:
        _, port = parsed.netloc.rsplit(":", 1)
        new_netloc = f"{override_host}:{port}"
    else:
        new_netloc = override_host
    return urlunparse(parsed._replace(netloc=new_netloc))


# Repos that are ALWAYS routed through Gitea.
#
# Empty by default. We rely on mitmproxy's pass-through behavior for upstream
# GitHub: anything not in url_rewrites passes to real github.com unmodified,
# and the public microsoft/* repos clone fine without auth.
#
# DELIBERATELY NOT INCLUDED: ("microsoft", "amplifier"). The matcher's prefix
# semantics mean a `microsoft/amplifier` rule silently captures every
# `microsoft/amplifier-*` URL (provider modules, foundation, etc.) and
# redirects them to Gitea where many don't exist (404). Verified by reading
# /opt/dtu/rewrite_addon.py inside a running DTU and matching mitmdump logs.
#
# `amplifier-bundle-digital-twin-universe` v0.2.0 (PR #7, 2026-05-05) added a
# `default_match_mode: boundary` feature that fixes this — the matcher then
# requires the prefix to terminate at /, ., ?, #, or end-of-path, and a dash
# is no longer a valid continuation. With v0.2.0+ installed AND
# `default_match_mode: boundary` set in the profile (which we already do in
# eval-base.yaml.tmpl), this list could safely include `microsoft/amplifier`.
# We keep it empty for compatibility with older DTU installs and because the
# pass-through approach is simpler and matches amplifier-tester's pattern.
ALWAYS_MIRROR_REPOS: tuple[tuple[str, str], ...] = ()


@dataclass
class RenderedProfile:
    yaml_text: str
    launch_vars: dict[str, str]


def all_repos_for_run(spec: RunSpec) -> list[tuple[str, str]]:
    """Return the deduplicated list of (owner, name) repos that need to be in Gitea
    for this run, in stable order (always-mirror first, then bundle, then overrides).

    The amplifier-core override does NOT use url_rewrites — it's handled via
    pypi_overrides instead — so it's excluded from the url_rewrites set returned
    here, but still included in the populate-Gitea path elsewhere.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []

    def add(owner: str, name: str) -> None:
        key = (owner, name)
        if key not in seen:
            seen.add(key)
            out.append(key)

    for owner, name in ALWAYS_MIRROR_REPOS:
        add(owner, name)
    add(spec.bundle.repo_owner, spec.bundle.repo_name)
    for eco in spec.config.ecosystem_overrides:
        if "/" in eco.repo:
            owner, name = eco.repo.split("/", 1)
        else:
            owner, name = "microsoft", eco.repo
        add(owner, name)
    return out


def _build_url_rewrites_rules(spec: RunSpec) -> str:
    """Produce the YAML lines under `url_rewrites.rules:` for this run.

    Skips amplifier-core (handled by pypi_overrides). Deduplicated.
    """
    lines: list[str] = []
    for owner, name in all_repos_for_run(spec):
        if name == "amplifier-core":
            continue
        lines.append(f"    - match: github.com/{owner}/{name}")
        lines.append(f"      target: ${{GITEA_URL}}/admin/{name}")
    return "\n".join(lines)


def _build_pypi_overrides_block(spec: RunSpec) -> str:
    """Produce the `pypi_overrides:` block when amplifier-core is in ecosystem_overrides."""
    core_override = next(
        (e for e in spec.config.ecosystem_overrides if e.repo.split("/")[-1] == "amplifier-core"),
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
    """Return (provision_files_block, setup_cmd_block) for settings.yaml deep-merge."""
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
                      if isinstance(item, (dict, list)):
                          key = json.dumps(item, sort_keys=True, default=str)
                      else:
                          key = f"{type(item).__name__}:{item}"
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
    pypi_overrides_block = _build_pypi_overrides_block(spec)
    overlay_files_block, overlay_setup_cmd = _build_settings_overlay_block(spec)

    rendered = (
        template_text.replace("{{ URL_REWRITES_RULES }}", url_rewrites_rules)
        .replace("{{ PYPI_OVERRIDES }}", pypi_overrides_block.rstrip())
        .replace("{{ SETTINGS_OVERLAY_FILES }}", overlay_files_block.rstrip())
        .replace("{{ SETTINGS_OVERLAY_SETUP_CMD }}", overlay_setup_cmd.rstrip())
    )

    launch_vars = {
        # Rewrite the host for sub-DTU access when running inside a nested Incus DTU.
        # See _resolve_dtu_gitea_url() and AMPLIFIER_EVAL_HARNESS_GITEA_HOST docs above.
        "GITEA_URL": _resolve_dtu_gitea_url(gitea_url),
        "GITEA_TOKEN": gitea_token,
        "BUNDLE_INSTALL_URL": spec.bundle.install_url(),
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
