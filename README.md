# amplifier-eval-harness

Test harness for running scenarios through [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) inside [Digital Twin Universe (DTU)](https://github.com/microsoft/amplifier-bundle-digital-twin-universe) environments.

Runs `bundles × scenarios × runs` matrices in isolated containers, captures per-run artifacts and metrics, supports swapping in local working trees of any ecosystem repo via Gitea mirroring.

## Status

**Pre-alpha (v0.2).** Sequential and parallel execution paths in place. First successful end-to-end smoke run against a live DTU on 2026-05-08 (Linux/Incus, foundation bundle, claude-opus-4-7); broader validation across configs/scenarios is still pending.

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

### Credential-isolated solver access (experimental, OpenAI endpoints only)

Default profiles/overlays can place provider credentials in solver-readable
state. To opt into the external credential proxy through the existing `run`
command and runner, add this block to your run config:

```yaml
credential_proxy:
  bind_ipv4: "10.0.0.1"  # Replace with the host bridge address reachable by the DTU.
  upstream_key_env: OPENAI_API_KEY
  upstream_base_url_env: OPENAI_BASE_URL
```

Set those two host environment variables before running. The base URL must be a
full HTTPS API base ending in `/v1`; no implicit vendor URL is selected. Parsing,
validation and `--dry-run` do not load their values. Configure the evaluated
OpenAI provider to use `${OPENAI_API_KEY}` and `${OPENAI_BASE_URL}`, not inline
credentials. The mode does not support Anthropic, Gemini or Copilot endpoints.

In this mode the runner:

- Removes **all** provider passthrough services from the rendered DTU profile,
  and launches with an allowlisted host subprocess environment.
- Starts the existing external proxy only around agent execution, giving the
  solver a temporary OpenAI token/base URL in a cleared execution environment.
- Records proxy IDs/states in a private, fsynced `proxy-resources.jsonl`. Proxy
  cleanup finishes before session collection or post-agent verification.
- Requires a fresh `0700` run directory. Unknown cleanup is a harness error;
  recovery files remain private. A captured CLI return is retained even when
  subsequent proxy cleanup fails. See `dtu-info.json`'s `extras` for these states.

Use only trusted, nonsecret profiles, settings overlays, bundles and fixtures,
and fresh DTUs without host management sockets or secret mounts. This option
does **not** sandbox malicious provisioning commands or arbitrary custom
profiles, remove Gitea installation credentials, or sanitize every user-authored
input. The selected upstream key is refused if found in the rendered profile,
launch variables or settings overlay; this is not a universal secret scanner.
Without `credential_proxy`, legacy provider passthrough behavior is unchanged.

Runner integration is covered by mocked CLI/lifecycle tests and a fake-key
control using the real external proxy and DTU health access (launch and agent
execution replaced with inert controls). That is not proof of a newly
provisioned, credentialed end-to-end run. Telemetry collection, artifact grading
and effective worker settings need their own verification.

`amplifier_eval_harness.credential_proxy.credential_proxy(...)` is a host-only
context manager for a separately mounted, pinned stock-NGINX Docker proxy:

- Keep the upstream key in a host-private `0700` directory / `0600` configuration,
  mounted read-only **only into the external proxy**, never the solver.
- Give the solver only the yielded local API base and temporary access token.
  Do not forward host credentials, personal configuration, management sockets,
  or the proxy's process/secret-mount namespace into the solver.
- Require resource recording: exact container ID before start; confirmed
  removal and absence before recording it reaped. Ambiguous creation is
  reconciled by recorded name, nonce label, and full ID. Unknown cleanup retains
  private recovery files and fails rather than claiming success.
- Allow only fixed upstream HTTPS POST endpoints ending in `/v1/responses` or
  `/v1/chat/completions`. TLS verification, literal upstream headers, denied
  queries/alternate targets, and stripped authentication challenges are part
  of the generated configuration.

NGINX reserves HTTP 499, so it cannot be mapped through `error_page`; its status
is preserved while a literal-key filter removes plain credential echoes from
response bodies. This is **not universal DLP**, encoded-secret protection, or
arbitrary-header sanitization. A trusted upstream and conventional container
isolation remain required assumptions.

The caller supplies an execution-UID-owned private directory, full upstream
API base ending in `/v1`, upstream key, explicit private IPv4 bind address, and
`record_resource(id, state)` callback (`active` / `reaped`). The helper does not
provision a solver or prove its isolation. Configuration, wire behavior, and
solver access boundaries require runtime checks; render/mock unit tests alone
do not establish them. Captures and credential-bearing configuration must never
be committed.

## Gitea instance pinning

By default the harness greedily reuses the first instance returned by `amplifier-gitea list`, falling back to creating a new one if none exist. That's fine on a solo dev machine but dangerous when multiple workspaces share a host — two harness invocations against the same Gitea race on `populate_repo` and may stomp on each other's mirrors.

To isolate a workspace, create a dedicated Gitea instance and pin to it:

```bash
amplifier-gitea create --port 10111 --name gitea-myworkspace
# {"id": "gitea-abcd1234", "port": 10111, ...}

# Pin via env var (per-invocation, no config edit required)
EVAL_HARNESS_GITEA_INSTANCE=gitea-abcd1234 amplifier-eval-harness run --config configs/smoke.yaml

# Or pin in the config itself
echo "gitea_instance_id: gitea-abcd1234" >> configs/myconfig.yaml
```

Resolution order (first wins): `EVAL_HARNESS_GITEA_INSTANCE` env var → YAML `gitea_instance_id` → greedy reuse of first listed instance → create new on port 10110. Pinned instances must already exist; the harness errors out rather than silently falling back.

## Running inside a nested Incus DTU

When you run `amplifier-eval-harness` from **inside** an Incus DTU (e.g. a `resolve-stack` instance), eval-sub-DTUs are spawned as **siblings** via the forwarded Incus socket. Their `localhost` is their own loopback — not the harness DTU's — so the default `http://localhost:<port>` GITEA_URL baked into sub-DTU profiles is unreachable. `uv tool install` inside the sub-DTU fails on any transitive `git+https://github.com/microsoft/...` dependency because mitmproxy's `url_rewrites` redirect those to the unreachable host.

Fix: set `AMPLIFIER_EVAL_HARNESS_GITEA_HOST` to the harness DTU's `eth0` IP. The harness will use this IP (instead of `localhost`) when passing `GITEA_URL` to eval-sub-DTU launch vars.

```bash
# Find the harness DTU's eth0 IP (run this inside the DTU):
ip -4 addr show eth0 | awk '/inet / {print $2}' | cut -d/ -f1
# e.g. 10.119.176.124

# Set before running the harness:
export AMPLIFIER_EVAL_HARNESS_GITEA_HOST=10.119.176.124
amplifier-eval-harness run --config configs/smoke.yaml
```

Local harness operations (Gitea API calls, mirroring, token fetches) are **not** affected — they still reach Gitea via `localhost` from the harness DTU's own perspective.

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

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
