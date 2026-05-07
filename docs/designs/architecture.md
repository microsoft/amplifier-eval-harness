# Amplifier Eval Harness — Architecture

**Status:** draft v0.2
**Last updated:** 2026-05-06

## Changes since v0.1

- CLI entry point renamed from `eval-harness` to `amplifier-eval-harness` (matches the package name).
- **Parallelism shipped.** `parallelism: N` (or `--parallelism N`) caps concurrent DTUs via a `ThreadPoolExecutor`. Per-thread log prefixing keeps interleaved output attributable to its run id. Repo population still runs sequentially before the parallel run loop because Gitea is shared state.
- `BundleSpec.source` parses `#subdirectory=<path>` fragments, supporting bundles like `amplifier-dev` that live as a single YAML file inside `amplifier-foundation/bundles/`. The fragment is preserved through the install URL inside the DTU, but the URL rewrite still keys on the bare repo name (so the same Gitea mirror serves any subdirectory bundle within the repo).
- URL rewrite rules are now deduplicated across the bundle, ecosystem overrides, and always-mirror entries.
- Default settings overlay is `settings/default-providers.yaml`, derived from the harness owner's `~/.amplifier/settings.yaml` with `provider-chat-completions` excluded (irrelevant inside DTUs).
- Profile passthrough expanded to forward `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `GOOGLE_API_KEY`, `GITHUB_TOKEN`, `GH_TOKEN` alongside the existing API keys. Unset host vars are simply not forwarded — no error.

## Purpose

Run scenarios through `amplifier` (the CLI) inside Digital Twin Universe (DTU) containers across a `bundles × scenarios × runs` matrix, capturing per-run metrics for baseline establishment and bundle comparison.

Used to:
- Baseline existing bundles (foundation, amplifier-dev) on a fixed scenario set
- Compare experimental/custom bundles against those baselines
- Sample multiple runs of the same combo to detect variance
- Optionally swap in local versions of any ecosystem repo (bundle, foundation, core, modules) via Gitea mirroring

## Design Principles

1. **One code path.** The harness *always* routes installs through Gitea via DTU `url_rewrites`. Whether a repo is upstream-only or locally-overridden is a per-repo flag — never two different runtime modes. This keeps the orchestration uniform and makes "swap in my local working tree" a trivial config flip.

2. **Config-driven, named configs.** A run is defined entirely by a YAML config file. Multiple configs live side-by-side in `configs/` (`smoke.yaml`, `baseline.yaml`, `experimental-X.yaml`, etc.). Invoke with `eval-harness run --config configs/<name>.yaml`. No hidden state, no global flags.

3. **Sparse → loaded.** A minimal config (1 bundle × 1 scenario × 1 run) is the default smoke shape. The same schema scales up to many bundles, scenarios, parallel runs, and ecosystem-level overrides without restructuring.

4. **Parallelism is a single config knob.** `parallelism: 1` runs sequentially (simpler logs, deterministic ordering); `parallelism: N` runs up to N DTUs concurrently via a thread pool. Each DTU is independent during the run loop; the only shared state is Gitea, which is populated sequentially before any DTU launches and read-only thereafter.

5. **Raw evidence over judgment.** v0 captures raw artifacts (json-trace stdout, transcript.jsonl, metadata.json, wall clock, exit code) and per-combo summaries. Quality scoring / LLM-as-judge is a later layer that reads these artifacts.

## Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│ eval-harness CLI (eval_harness.cli)                                     │
│   eval-harness run --config configs/smoke.yaml                          │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Config loader (config.py)                                               │
│  - Parse YAML, validate, expand matrix → list of RunSpec                │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Session init (runner.py / gitea.py)                                     │
│  - Find or create Gitea instance (amplifier-gitea list/create)          │
│  - Generate fresh API token                                             │
│  - For each unique repo in the matrix:                                  │
│      • Upstream-only: mirror-from-github (if not already present)       │
│      • Local override: snapshot working tree → push to Gitea            │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Per-run loop (runner.py)                                                │
│  For each RunSpec (bundle, scenario, run_index):                        │
│    1. profile.render_profile(...) → profile YAML to disk                │
│    2. amplifier-digital-twin launch <profile> --var KEY=VAL ...         │
│    3. amplifier-digital-twin check-readiness <id> (poll until ready)    │
│    4. (optional) push scenario workspace files into /workspace          │
│    5. amplifier-digital-twin exec <id> -- amplifier run \               │
│         --bundle <name> --output-format json-trace "<prompt>"           │
│    6. Capture stdout (json-trace) + exit code                           │
│    7. amplifier-digital-twin file-pull <id> /root/.amplifier/projects/  │
│    8. amplifier-digital-twin destroy <id>                               │
│    9. results.write_run_artifacts(...)                                  │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Results aggregation (results.py)                                        │
│  - Per-run dir: result.json, stderr.log, transcript.jsonl, metadata.json│
│  - Aggregate: manifest.json, summary.csv, summary.md                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Config Schema (sparse → loaded)

### Sparse (smoke)
```yaml
# configs/smoke.yaml
bundles:
  - name: foundation
    source: git+https://github.com/microsoft/amplifier-foundation@main

scenarios:
  - id: explain-repo

runs_per_combo: 1
```

### Loaded
```yaml
# configs/full-experimental.yaml
output_dir: ./eval-results/2026-05-06-experimental
parallelism: 1                       # v0: always 1; reserved for v1
amplifier_install_ref: main          # branch/SHA of microsoft/amplifier
launch_timeout_s: 600
exec_timeout_s: 900
keep_dtu_on_failure: true            # for postmortem inspection
keep_dtu_on_success: false

# Provider/model config — pushed into DTU and deep-merged with ~/.amplifier/settings.yaml
settings_overlay: ./settings/anthropic-claude-sonnet.yaml

# Optional: ecosystem-level overrides applied to ALL runs in this config.
# Each entry adds a url_rewrites rule (or pypi_overrides for amplifier-core).
ecosystem_overrides:
  - repo: microsoft/amplifier-foundation
    local_path: ../amplifier-foundation
  # - repo: microsoft/amplifier-core
  #   local_path: ../amplifier-core      # special: triggers pypi_overrides wheel build
  # - repo: microsoft/amplifier-module-tool-bash
  #   git_ref: my-branch                  # upstream from a non-main ref

bundles:
  - name: foundation
    source: git+https://github.com/microsoft/amplifier-foundation@main
  - name: amplifier-dev
    # amplifier-dev lives as a single YAML inside amplifier-foundation/bundles/.
    # The #subdirectory= fragment is preserved end-to-end into `amplifier bundle add`.
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/amplifier-dev.yaml
  - name: my-experiment
    source: file://../amplifier-bundle-experiment    # local working tree → snapshot push
    # Local sources can also use #subdirectory=, e.g.:
    # source: file://../amplifier-foundation#subdirectory=bundles/my-experiment.yaml

scenarios:
  - id: explain-repo
    prompt_path: scenarios/explain-repo/prompt.md
    workspace_path: scenarios/explain-repo/workspace/   # optional: seeded into /workspace
  - id: refactor-function
    prompt_path: scenarios/refactor-function/prompt.md
    workspace_path: scenarios/refactor-function/workspace/

runs_per_combo: 3
```

## Output Layout

```
eval-results/2026-05-06-experimental/
├── manifest.json                       # full resolved config, harness version, start/end times, gitea instance
├── summary.csv                         # one row per run
├── summary.md                          # human-readable rollup
└── runs/
    └── {bundle}__{scenario}__r{N}/
        ├── run-spec.json               # the resolved RunSpec for this run
        ├── result.json                 # parsed json-trace from amplifier run
        ├── stdout.txt                  # raw json-trace stdout
        ├── stderr.log                  # diagnostics from amplifier
        ├── exit_code                   # exit code (1 line)
        ├── wall_clock_s                # harness-measured wall clock
        ├── dtu-info.json               # instance id, launch/exec/teardown timings
        ├── profile.yaml                # the rendered profile used for this run
        └── session/                    # pulled from /root/.amplifier/projects/
            └── -workspace/sessions/<uuid>/
                ├── transcript.jsonl
                └── metadata.json
```

## Repo Override Mechanism (Gitea-always)

Every harness session starts by ensuring a Gitea instance and pushing every relevant repo into it. Two paths:

1. **Upstream-only** (`source: git+https://...` or no override):
   ```bash
   curl -sf -H "Authorization: token $GITEA_TOKEN" \
        "$GITEA_URL/api/v1/repos/admin/<repo>" >/dev/null \
     || amplifier-gitea mirror-from-github $GITEA_ID --github-repo https://github.com/microsoft/<repo>
   ```

2. **Local override** (`local_path: ...` or `source: file://...`):
   ```bash
   # NEVER mutate the user's working tree. Snapshot into a temp clone, push from there.
   SNAP=$(mktemp -d)/<repo>
   git clone --local --no-hardlinks <local_path> $SNAP
   ( cd <local_path> && git ls-files -z --cached --modified --others --exclude-standard ) \
     | rsync -a --files-from=- --from0 <local_path>/ $SNAP/
   ( cd <local_path> && git ls-files -z --deleted ) \
     | (cd $SNAP && xargs -0 --no-run-if-empty rm -f)
   cd $SNAP
   git -c user.email=eval@local -c user.name="Eval Snapshot" add -A
   git -c user.email=eval@local -c user.name="Eval Snapshot" commit --allow-empty -m "Eval snapshot"
   git remote add gitea http://admin:$GITEA_TOKEN@localhost:$GITEA_PORT/admin/<repo>.git
   git push gitea HEAD:main --force
   rm -rf $(dirname $SNAP)
   ```

The DTU profile uses `url_rewrites` (with `default_match_mode: boundary`) to redirect every `github.com/microsoft/<repo>` install URL to `${GITEA_URL}/admin/<repo>`. `UV_NO_GITHUB_FAST_PATH=true` is set automatically by DTU when `url_rewrites` is present, ensuring uv routes through the proxy.

## Profile Generation

`profiles/eval-base.yaml.tmpl` is a parameterized template with `${VAR}` placeholders. The harness renders it per-run by:
1. Reading the template
2. Computing the set of `url_rewrites.rules` from the bundle + ecosystem_overrides
3. Computing the `pypi_overrides` if `amplifier-core` is overridden
4. Writing the rendered profile to the run's output dir
5. Passing `--var GITEA_URL=... GITEA_TOKEN=... BUNDLE_REPO=... BUNDLE_NAME=...` at launch

## Settings Overlay

If `settings_overlay:` is set, the file is included in `provision.files` (pushed to `/tmp/settings_overlay.yaml`) and a `setup_cmds` step runs an inline Python deep-merge against `/root/.amplifier/settings.yaml` after the base settings are written.

## What v0 Captures

| Metric | Source | How |
|---|---|---|
| Wall clock (harness-measured) | runner.py timer around `exec` | Always |
| Wall clock (amplifier-internal) | `metadata.duration_ms` in json-trace | When status=success |
| Tool call count | `metadata.total_tool_calls` | When status=success |
| Agent invocations | `metadata.total_agents_invoked` | When status=success |
| Per-tool execution trace | `execution_trace[]` array | When status=success |
| Exit code | `amplifier run` exit | Always |
| Status | `result.status` ("success" or "error") | When valid json-trace |
| Error message | `result.error` if status=error | When status=error |
| Full transcript | `transcript.jsonl` from session dir | Pulled via file-pull |
| Final response | `result.response` | When status=success |
| Bundle / model used | `result.bundle` / `result.model` | When status=success |

## What v0 Explicitly Does NOT Capture

- **Token counts** — not exposed by amplifier CLI; would require core instrumentation
- **Cost** — same reason
- **Quality scores** — manual or LLM-as-judge later, reading the captured artifacts

## Open Questions

- **Workspace fixture seeding mechanism**: scenarios with `workspace_path:` push that directory's contents into `/workspace` inside the DTU via `amplifier-digital-twin file-push <id> <local> /workspace/`. Empirical verification on first smoke run.
- **`amplifier-core` wheel cache**: when `amplifier-core` is in `ecosystem_overrides`, every run re-builds the wheel via `wheel_from_git`. A host-side wheel cache keyed on (repo, ref) would eliminate the rebuild for matrix runs that don't change core. Punted; not relevant until core overrides are exercised.
- **Token / cost capture**: the amplifier CLI does not surface tokens or cost. Adding it would require core instrumentation (provider hook reading the API response usage) or post-hoc parsing of the per-provider HTTP transcript. Not in scope for v0.x; reconsider when the matrix size makes manual reading impractical.
