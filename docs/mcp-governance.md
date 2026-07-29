# MCP and Host-Permission Governance

Two different MCP surfaces flow through Agents Shipgate, and they are
governed by two different mechanisms. This page is the map.

## Surface 1: the agent project's tool surface (what your AI agent can do)

MCP **exports** declared in `shipgate.yaml` (`tool_sources: - type: mcp`)
are scanned as the agent's tool inventory: schemas, scopes, approval /
confirmation / idempotency policy coverage, wildcard exposure, risk tags.
This is the original Tool-Use Readiness review — see
[`manifest-v0.1.md`](manifest-v0.1.md) and [`checks.md`](checks.md).

The capability delta between base and head (`capability_change` in
`report.json`, plus the lock/diff artifacts from `agents-shipgate
capability`) tracks how that tool surface changes per PR — see
[`capability-standard.md`](capability-standard.md).

## Surface 2: the coding agent host's own grants (what your coding agent can do)

A PR that edits the **host configuration** of a coding agent changes what
that agent is allowed to do in your repo — without touching a single line
of agent-product code. These files are capability grants:

| File | Host | Grants |
|---|---|---|
| `.mcp.json` | Claude Code (project scope) | MCP servers: commands, URLs, env passthrough |
| `.claude/settings.json`, `.claude/settings.local.json` | Claude Code | `permissions.allow` / `deny` rules, hooks, env |
| `.cursor/mcp.json`, `.cursor/cli.json` | Cursor | MCP servers and Shell/Read/Write permission rules |
| `.vscode/mcp.json` | VS Code | MCP servers |
| `.codex/config.toml`, `.codex/hooks.json` | Codex | network profile, MCP auto-approval, hooks (see the `SHIP-CODEX-BOUNDARY-*` checks) |
| `.github/workflows/*.yml` | CI | workflow `permissions:`, triggers |

One normalized static boundary assessment feeds two projections:

1. **Trust-root flagging** (`SHIP-VERIFY-TRUST-ROOT-TOUCHED`): any change
   to a protected surface routes the PR to human review. Suppression-
   immune. This is the coarse layer — "a hand touched the boundary."
2. **Host-boundary semantics** (`SHIP-HOST-BOUNDARY-*` and
   `SHIP-CODEX-BOUNDARY-*`, diff-aware): `shipgate check`, MCP, and `verify`
   consume the same old-vs-new classification.
   This is the fine layer — "the boundary moved, in this direction."

### Host-boundary checks

| Check | Fires when | Outcome |
|---|---|---|
| `SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED` | A new MCP server appears in `.mcp.json` / `.cursor/mcp.json` / `.vscode/mcp.json` | human review |
| `SHIP-HOST-BOUNDARY-MCP-SERVER-CHANGED` | An existing server's `command` / `args` / `url` / `env` keys change | human review |
| `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW` | A wildcard-shaped rule (e.g. `Bash(*)`) is added to `permissions.allow` | **blocked** |
| `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` | Any new `permissions.allow` entry | human review |
| `SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED` | A `permissions.deny` entry is removed | human review |
| `SHIP-HOST-BOUNDARY-HOOK-CHANGED` | Claude Code hooks added or modified | human review |
| `SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL` | A workflow gains `permissions: write-all` | **blocked** |
| `SHIP-HOST-BOUNDARY-WORKFLOW-PERMISSIONS-EXPANDED` | A workflow scope moves `read` → `write` or gains a new write scope | human review |
| `SHIP-HOST-BOUNDARY-PULL-REQUEST-TARGET-ADDED` | A workflow gains the `pull_request_target` trigger | human review (critical) |
| `SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED` | A changed host config cannot be parsed | human review (fail closed) |

Like the `SHIP-VERIFY-*` and `SHIP-CODEX-BOUNDARY-*` families, these
checks are **suppression-immune** (`checks.ignore` cannot hide them) and
**floor-protected** (severity cannot be downgraded past the floor). The
cheapest reward-hack — an agent granting itself `Bash(*)` or deleting the
gate — is a blocking, unsuppressible finding.

### What this is not

- Not a runtime MCP gateway: nothing is intercepted at call time. The
  governance runs at PR time on declared configuration.
- Not server vetting: Shipgate flags that a server was added and what it
  can reach; whether the server itself is trustworthy is a human review
  question (pin versions, audit the package, restrict env passthrough).
- Not a substitute for the OS sandbox: host grants are evaluated as
  declared; runtime enforcement belongs to the host.

## Reviewer guidance

When `SHIP-HOST-BOUNDARY-*` fires, review like a permission request, not
like code:

1. **Who benefits?** A new MCP server / allow rule should map to a task
   the team actually asked for.
2. **Scope check.** Prefer `Bash(npm test:*)` over `Bash(*)`; prefer
   explicit tool allowlists over server-wide approval; prefer `read` over
   `write` workflow scopes.
3. **Env passthrough.** Server `env` keys are listed in the finding
   evidence (values are never copied). New secret-bearing keys deserve
   the same scrutiny as a new credential.
4. **`pull_request_target`.** Combined with checkout of PR code, this is
   the classic CI secrets-exfiltration shape. Require a written
   justification.

## Zero-config audit

To inventory host grants without a `shipgate.yaml` (for example, on a
repo you are evaluating), use `shipgate audit --host`. It reads the same
normalized repository host surfaces as `check` and prints a one-page Markdown
inventory without writing anything unless `--out` or `--save-baseline` is
selected.
For CI or fleet ingestion, emit the versioned JSON artifact:

```bash
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

Repository scope is deterministic and default. The explicit
`--scope local-static` option additionally reads supported user and file-based
managed configuration. It does not execute hosts or policy helpers and still
excludes invocation flags, transient approvals, UI/session state, remote
managed settings, runtime enforcement, and actual tool behavior.

The payload includes `host_grants_inventory_schema_version: "0.2"`, typed
redacted `grants[]`, `artifacts[]`, `host_coverage[]`, `issues[]`, and
`excluded_scopes[]`, and validates against
[`host-grants-inventory-schema.v0.2.json`](host-grants-inventory-schema.v0.2.json).
An incomplete inventory cannot be saved as a baseline.
The exact host, path, scope, and non-claim matrix is published in
[`host-boundary-support.md`](host-boundary-support.md).

## Host-grant drift detection

PR-time checks only see grants that change inside a reviewed diff. Host
grants also change outside that loop: a coding agent (or a hurried
human) edits `.mcp.json`, `.claude/settings.json`, or a workflow
directly on the default branch, and nothing re-reviews the new
authority. Drift detection closes that loop with two operations on the
same inventory:

```bash
# 1. After a human reviews the current grants, record them as the
#    acknowledged state (writes .agents-shipgate/host-grants.json):
shipgate audit --host --save-baseline

# 2. On a schedule (or pre-commit), compare current grants against the
#    acknowledged state; exit 20 on any drift:
shipgate audit --host --drift --fail-on-drift
```

The v0.2 baseline is content-only (no timestamps, raw secrets, or machine
paths), so
re-saving an unchanged state is byte-identical, and it is meant to be
committed: `.agents-shipgate/` is already a verify trust-root surface,
so a PR that edits the snapshot is release-visible like any other
policy change. The stored `inventory_sha256` is verified on every
`--drift` load — a hand-edited or corrupted baseline fails closed instead of
silently reporting no drift. A v0.1 baseline, scope mismatch, or incomplete
comparison is reported as `comparison_status="incomparable"`; advisory mode
exits 0 and `--fail-on-drift` exits 20. An incomparable result exposes no
runnable recovery command: review the existing baseline and move, remove, or
repair it before explicitly accepting the current host grants.

MCP server and hook entries carry a `config_sha256` over their full
configuration. Inside `env`/`headers`, only values under
**secret-looking keys** (matched against the shared sensitive-key
vocabulary: token, secret, password, api_key, authorization, …) are
redacted before hashing — env values are often grant-shaping config,
not just credentials. So editing what an existing server or hook can do
(args, commands, matchers, URL, env/header keys, or a non-secret value
like `READ_ONLY=false`) is drift; rotating `GITHUB_TOKEN` or an
`Authorization` header is not. Misclassification fails safe: a secret
under an unconventional key name causes drift noise on rotation, never
a blind spot — and raw values are never stored either way, only the
hash.

The drift report lists added/removed/changed entries per category and
names **expansion signals** — the drift shapes that broaden coding-agent
authority: a new MCP server, a new allow rule (wildcards flagged), a
**removed** `deny` or `ask` rule (removals broaden here — this is why
the gate fires on *any* drift rather than trying to classify
"safe" directions), a new hook event, a workflow gaining write scopes or
`pull_request_target`, or Codex config appearing.

After reviewing a legitimate drift, re-acknowledge with
`--save-baseline` again. Do not re-save to silence drift nobody
reviewed. A scheduled CI recipe is at
[`examples/github-actions/12-host-grant-drift.yml`](../examples/github-actions/12-host-grant-drift.yml).
