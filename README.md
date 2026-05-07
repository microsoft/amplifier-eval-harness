# amplifier-eval-harness

Test harness for running scenarios through [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) inside [Digital Twin Universe (DTU)](https://github.com/microsoft/amplifier-bundle-digital-twin-universe) environments.

Runs `bundles × scenarios × runs` matrices in isolated containers, captures per-run artifacts and metrics, supports swapping in local working trees of any ecosystem repo via Gitea mirroring.

## Status

**Pre-alpha.** v0 scaffolding. The smoke config runs end-to-end but has not been validated against real DTU launches yet.

## Quick start

```bash
# Prerequisites:
#   - amplifier CLI installed              (uv tool install git+https://github.com/microsoft/amplifier)
#   - amplifier-bundle-gitea available     (provides amplifier-gitea CLI)
#   - amplifier-bundle-digital-twin-universe available (provides amplifier-digital-twin CLI)
#   - Docker running                       (for Gitea container)
#   - Incus configured                     (for DTU containers)
#   - ANTHROPIC_API_KEY set in the environment

# Install the harness
uv tool install --from . eval-harness

# Run the smoke config (1 bundle × 1 scenario × 1 run)
eval-harness run --config configs/smoke.yaml

# Inspect results
ls eval-results/<timestamp>/runs/*/
cat eval-results/<timestamp>/summary.md
```

## Configs

Configs live in `configs/`. Add new ones with descriptive names; pick which to run via `--config`.

| Config | Purpose |
|---|---|
| `smoke.yaml` | Inner-dev-loop. 1 bundle × 1 scenario × 1 run. |
| `baseline.yaml` | Foundation + amplifier-dev × explain-repo × 3 runs. |

See [docs/designs/architecture.md](docs/designs/architecture.md) for the full schema.

## Scenarios

Scenarios live in `scenarios/<id>/`. Each scenario has a `prompt.md` and an optional `workspace/` directory of fixture files seeded into `/workspace` inside the DTU before the prompt runs.

| Scenario | What it exercises |
|---|---|
| `explain-repo` | File reading, code summarization. Stable across runs. |

## Architecture in 60 seconds

1. Read config → expand matrix into list of `RunSpec`
2. Ensure Gitea instance, push all relevant repos into it (upstream mirror or local snapshot)
3. For each `RunSpec`: render DTU profile → launch → exec `amplifier run --bundle X --output-format json-trace "..."` → capture stdout + exit + session files → destroy
4. Write per-run artifacts and aggregate summary

The harness *always* routes through Gitea, even when no local overrides are configured. This keeps one code path; swapping in a local working tree is a one-line config flip.

## Limitations (v0)

- **Sequential.** No parallel run pool yet.
- **No token/cost capture.** amplifier CLI doesn't surface these. Wall clock, tool calls, exit code only.
- **No quality scoring.** Raw artifacts only; bring your own judgment or LLM-as-judge later.
- **Single provider per config.** Multi-provider runs require separate configs.

## Layout

```
.
├── README.md
├── pyproject.toml
├── docs/designs/architecture.md       # source-of-truth design doc
├── eval_harness/                      # the CLI package
├── profiles/eval-base.yaml.tmpl       # parameterized DTU profile
├── configs/                           # ready-to-run named configs
├── scenarios/                         # prompt + workspace fixtures
└── settings/                          # provider/model overlay YAMLs
```

## License

MIT (TBD)
