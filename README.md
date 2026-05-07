# amplifier-eval-harness

Test harness for running scenarios through [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) inside [Digital Twin Universe (DTU)](https://github.com/microsoft/amplifier-bundle-digital-twin-universe) environments.

Runs `bundles × scenarios × runs` matrices in isolated containers, captures per-run artifacts and metrics, supports swapping in local working trees of any ecosystem repo via Gitea mirroring.

## Status

**Pre-alpha (v0.2).** Sequential and parallel execution paths in place. Smoke-tests have not yet been run against a live DTU.

## Quick start

```bash
# Prerequisites:
#   - amplifier CLI installed (uv tool install git+https://github.com/microsoft/amplifier)
#   - amplifier-bundle-gitea (provides amplifier-gitea CLI)
#   - amplifier-bundle-digital-twin-universe v0.2.0+ (provides amplifier-digital-twin CLI).
#       v0.1.x silently ignores `default_match_mode: boundary`; URL prefix collisions
#       with sibling repos can over-match. PR #7 (merged 2026-05-05) fixes it.
#   - Docker running (for Gitea container) + Incus (for DTU containers)
#   - At least one provider env var set (ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, GITHUB_TOKEN…)

# Install
uv tool install --from . amplifier-eval-harness

# Sanity check (no DTU launches)
amplifier-eval-harness validate --config configs/smoke.yaml

# Smoke run (1 bundle × 1 scenario × 1 run, sequential)
amplifier-eval-harness run --config configs/smoke.yaml

# Baseline (foundation + amplifier-dev × 3 runs each, up to 2 in parallel)
amplifier-eval-harness run --config configs/baseline.yaml

# Override parallelism at the CLI without editing the config
amplifier-eval-harness run --config configs/baseline.yaml --parallelism 4

# Dry run (just expand and print the matrix)
amplifier-eval-harness run --config configs/smoke.yaml --dry-run
```

Output lands in `eval-results/<config-stem>-<timestamp>/`. Read `summary.md` first.

## Configs

Configs live in `configs/`. Add new ones with descriptive names; pick which to run via `--config`.

| Config | Purpose |
|---|---|
| `smoke.yaml` | Inner-dev-loop. 1 bundle × 1 scenario × 1 run, sequential. |
| `baseline.yaml` | foundation + amplifier-dev × explain-repo × 3 runs each, parallelism=2. |

See [docs/designs/architecture.md](docs/designs/architecture.md) for the full schema and run flow.

## Scenarios

Scenarios live in `scenarios/<id>/`. Each scenario has a `prompt.md` and an optional `workspace/` directory of fixture files seeded into `/workspace` inside the DTU before the prompt runs.

| Scenario | What it exercises |
|---|---|
| `explain-repo` | File reading, code summarization. Stable across runs. |

## Settings overlays

Per-config provider/model selection happens via a YAML overlay deep-merged into the container's `~/.amplifier/settings.yaml` at provision time. The default overlay (`settings/default-providers.yaml`) is lifted from the harness owner's `~/.amplifier/settings.yaml` minus `provider-chat-completions` (which is local-only and not relevant inside DTUs).

To use a different model mix, copy the overlay, edit, and point the config's `settings_overlay:` at the new file.

## Architecture in 60 seconds

1. Read config → expand `bundles × scenarios × runs_per_combo` into a flat list of `RunSpec`.
2. Ensure a Gitea instance, push every relevant repo into it (upstream mirror or local working-tree snapshot).
3. For each `RunSpec` (sequential when `parallelism: 1`, ThreadPoolExecutor-bounded when `> 1`):
   - Render a parameterized DTU profile.
   - Launch DTU; wait for readiness; push scenario workspace fixture; deep-merge settings overlay.
   - `exec amplifier run --bundle <name> --output-format json-trace "<prompt>"` and capture stdout, stderr, exit code.
   - `file-pull` the session directory; destroy DTU (or keep on failure).
4. Aggregate results into `manifest.json`, `summary.csv`, `summary.md`.

Always routes installs through Gitea — one code path, swapping in a local working tree is a per-repo flag rather than a runtime mode switch.

## Parallelism

`parallelism: N` in the config (or `--parallelism N` on the CLI) caps the number of concurrent DTUs. Each running DTU consumes ~1.5–2 GB of RAM and a CPU core during provisioning. Pick a value your machine can sustain.

Gitea is shared but read-mostly during the run loop — repo population happens once, sequentially, before any DTU launches. Output from concurrent runs is interleaved on stderr; each line is prefixed with the run id for traceability.

## Limitations (v0.2)

- **No token / cost capture.** amplifier CLI doesn't surface these. Wall clock, tool call count, agent invocations, full transcript, and per-tool execution trace are captured.
- **No quality scoring.** Raw artifacts only; LLM-as-judge / rubric scoring is a separate later layer that reads `runs/*/result.json`.
- **Single provider mix per config.** Different model setups require different `settings_overlay:` files (and therefore different configs).

## Layout

```
.
├── README.md
├── pyproject.toml
├── docs/designs/architecture.md       # source-of-truth design doc
├── eval_harness/                      # the CLI package
│   ├── cli.py        # eval-harness CLI (run / validate / gitea-status)
│   ├── config.py     # YAML schema + matrix expansion
│   ├── gitea.py      # Gitea instance lifecycle + mirror/snapshot push
│   ├── profile.py    # Parameterized profile rendering (url_rewrites dedup, settings overlay splice)
│   ├── runner.py     # Per-run flow (launch / exec / file-pull / destroy)
│   ├── results.py    # Per-run JSON, summary CSV/MD, manifest
│   └── _log.py       # Thread-local log prefix for parallel runs
├── profiles/eval-base.yaml.tmpl       # parameterized DTU profile
├── configs/                           # ready-to-run named configs
├── scenarios/                         # prompt + workspace fixtures
└── settings/                          # provider/model overlay YAMLs
```

## License

MIT (TBD)
