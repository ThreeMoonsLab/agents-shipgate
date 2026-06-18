# Agents Shipgate Agent-Native Protocol

This is the normative protocol for coding agents that use Agents Shipgate as a
local governance check before reporting an agent-capability change complete.

Agents only need one command and one JSON schema:

```bash
shipgate check --agent codex --workspace . --format agent-json
```

Use `--agent claude-code` for Claude Code and `--agent cursor` for Cursor.
The command writes no repo artifacts by default. It prints one JSON object to
stdout: `agent_result_v1`.

`agents-shipgate verify` and `agents-shipgate-reports/report.json` remain the
full CI and reviewer substrate. Coding agents should use them for committed PR
verification and reviewer evidence, but their local control loop is
`shipgate check` plus `agent_result_v1`.

## Command

Default local check:

```bash
shipgate check --agent codex --workspace . --format agent-json
```

Committed diff check:

```bash
shipgate check --agent codex --workspace . --base origin/main --head HEAD --format agent-json
```

Fixture or MCP-provided diff:

```bash
shipgate check --agent codex --workspace . --diff change.diff --format agent-json
shipgate check --agent codex --workspace . --diff - --format agent-json
```

The no-`--diff` form resolves a git diff locally. With no `--base` or `--head`,
it reads local uncommitted tracked changes. With `--base` and `--head`, it reads
`base...head`. Supplying only one of `--base` or `--head` is invalid; omit both
for local work or provide both for committed refs. Shipgate never fetches refs.

## Result Schema

The stdout object has:

- `schema_version: "agent_result_v1"`
- `agent: "codex" | "claude-code" | "cursor"`
- `decision: "allow" | "warn" | "block" | "require_review"`
- `completion_allowed`
- `must_stop`
- `first_next_action`
- `human_review`
- `repair`
- `policy`
- `source_artifacts`
- `audit_id`
- `exit_code_hint`

Consumers must make decisions from JSON fields, never from prose or Markdown.
The stable schema is `docs/agent-result-schema.v1.json`. In v0.13.0, `policy`
is required for every in-tree producer under the existing `agent_result_v1`
schema name; consumers that validate v0.12.0-era objects should update the
schema with the package. `decision`, `completion_allowed`, `must_stop`,
`first_next_action`, `human_review`, `repair`, and `policy` are the control
signals. `risk_level` is explanatory and may differ between local-check and
verifier projections for the same allowed decision.

With `--format agent-json`, schema-valid results normally exit `0` even when
`decision` is `block` or `require_review`; wrappers must switch on
`decision`, `completion_allowed`, and `must_stop`, not `$?`. Diff-input setup
failures also return a `block` result with `exit_code_hint: 2`. Unsupported
CLI shape errors such as an invalid `--agent` or `--format` still exit nonzero
before an `agent_result_v1` object exists.

## State Machine

| `decision` | Agent action |
|---|---|
| `allow` | Continue. Completion is allowed. |
| `warn` | Continue, but surface the warning in the final task summary. |
| `block` with `first_next_action.actor="coding_agent"` and `repair.safe_to_attempt=true` | Apply only the listed repair, then rerun the exact command in `repair.command` or `first_next_action.command`. |
| `block` with `first_next_action.kind="install"` | The gate cannot run: the binary is missing or stale. Run `first_next_action.command` (install or upgrade), then rerun `shipgate check`. Do not report completion until a rerun returns `allow` or `warn`. |
| `block` with `first_next_action.actor="human"` | Stop. Do not continue, suppress, waive, or weaken policy. |
| `require_review` | Stop and ask for human review. |

`must_stop=true` is an explicit stop boundary. An agent must not claim the task
complete when `must_stop=true`, except to report that human review or install is
required.

The `kind="install"` block is distinct from the repair loop below: it does not
fix a finding, it restores a working gate. `repair.safe_to_attempt` is `false`
(there is no finding to repair), the action routes to the coding agent, and
`completion_allowed` is `false`. See [Missing Install](#missing-install) and
[Stale Install](#stale-install) for the two cases and their fixtures.

## Repair Loop

Agents may repair only when all of these are true:

- `decision="block"`
- `first_next_action.actor="coding_agent"`
- `first_next_action.kind="repair"`
- `repair.safe_to_attempt=true`
- the repair does not violate `repair.forbidden_shortcuts`

Every agent-safe repair must include a rerun command. After applying the
repair, run that command and parse the next `agent_result_v1`. Completion is
allowed only after a rerun returns `decision="allow"` or `decision="warn"`.

Human-only authority gaps are never agent-repairable. Approval, confirmation,
idempotency, broad-scope, prohibited-action, waiver, baseline, suppression,
severity downgrade, policy-pack, trace-evidence, and release-policy decisions
must set `human_review.required=true` and stop the agent.

`repair.forbidden_shortcuts` is present on every result, including `allow`, so
agents have the same trust-root boundary even when no finding fires.

## Coverage

`shipgate check` is boundary-scoped: it evaluates host and trust-root surfaces
(Codex/host config, MCP approvals, the Shipgate CI gate, agent instructions,
policy, and skills) from the diff. It does **not** compute the tool-use
capability delta — that is `verify`'s job, and `release_decision.decision`
remains the one authoritative capability gate.

Treat `check` as necessary but not sufficient for capability-expanding diffs.
If a change adds dynamic, undeclared, or otherwise ambiguous tool capability,
do not treat `decision="allow"` as merge readiness; run `verify` and read
`release_decision.decision`.

So that `check` never disagrees with that gate, a clean boundary result over a
diff that changes a **manifest-declared tool source** (a `tool_sources[].path`
entry — the changed file equals it, or sits under it when the path is a
scanned directory like an `openai_agents_sdk` agents folder) does not return
`allow`. It returns `decision="warn"` with
`first_next_action.kind="warn"` routing to `verify`, plus a
`diagnostics[].code="capability_change_requires_verify"` marker and a
`trace[].step="coverage"` event. Completion is still allowed, but the agent
must run `verify` for the capability merge gate before reporting done. This
keeps `check` from green-lighting a capability change it did not evaluate. A
diff that only touches boundary surfaces or unrelated files (docs, tests)
still returns `allow`.

## Human Boundary

The human approval boundary is explicit:

- `human_review.required=true` means a person must decide.
- `required_reviewers[]` names reviewer roles.
- `first_next_action.actor="human"` means the coding agent must stop.
- `must_stop=true` means the agent cannot report completion.

Do not bypass Shipgate by suppressing findings, lowering severity, expanding a
baseline, adding a waiver, removing CI, weakening agent instructions, or editing
Shipgate policy to pass. Those edits are trust-root changes and must block or
route to human review.

## Policy Discovery

Policy discovery is deterministic:

1. `--policy <path>` wins.
2. Then `policies/codex-boundary.shipgate.yaml` in the workspace.
3. Then the packaged default policy.

Every result includes:

- `policy.source`
- `policy.id`
- `policy.version`
- `policy.snapshot_sha256`
- `policy.discovery[]`

Invalid explicit policy and unknown explicit policy fields fail closed to
`require_review`. A diff that weakens or deletes Shipgate policy emits
`decision="block"`.

## Missing Install

If the `shipgate` or `agents-shipgate` binary is unavailable, the agent cannot
run the command that would produce JSON. In that one case, agent instructions
must surface a schema-valid `agent_result_v1` object. Its routing fields must
look like:

```json
{
  "schema_version": "agent_result_v1",
  "decision": "block",
  "first_next_action": {
    "actor": "coding_agent",
    "kind": "install",
    "command": "pipx install agents-shipgate",
    "why": "Agents Shipgate is not installed."
  }
}
```

Use `examples/agent-protocol/expected/missing-install.json` as the full
fixture. Once a current version is installed, all other errors must come from
Shipgate JSON rather than agent-authored prose.

## Stale Install

A binary that is present but older than the required `>=0.13.0` is the other
fail-safe case: a stale copy lingering on `PATH` can emit an outdated schema or
lack the command this protocol expects (a plain `pipx install` is a no-op over
an already-installed older build). Confirm the version first with
`agents-shipgate --version`; if it is older than required, do not trust the
stale binary's output. Surface a schema-valid `agent_result_v1` object that
routes to an upgrade:

```json
{
  "schema_version": "agent_result_v1",
  "decision": "block",
  "first_next_action": {
    "actor": "coding_agent",
    "kind": "install",
    "command": "pipx upgrade agents-shipgate",
    "why": "Installed Agents Shipgate is older than the required >=0.13.0."
  }
}
```

Use `examples/agent-protocol/expected/stale-install.json` as the full fixture.
The `install` action kind also carries upgrades, so consumers switch on the
same routing fields as the missing-install case; only the command differs
(`pipx upgrade agents-shipgate`, or `python -m pip install -U
"agents-shipgate>=0.13"`). Rerun `shipgate check` after upgrading.

## Self-Check

After install or upgrade:

```bash
agents-shipgate self-check --json
```

Self-check validates bundled fixtures, core CLI surfaces, and the
`agent_result_v1` module import. It is diagnostic only; it is not a replacement
for `shipgate check` on the active diff.

## Optional MCP Tool

The optional extra `agents-shipgate[mcp]` exposes a read-only MCP server with
static projection tools:

```text
shipgate.check
shipgate.preflight
shipgate.explain
shipgate.capabilities
```

Input:

```json
{
  "agent": "codex",
  "workspace": ".",
  "diff_text": "... unified diff ...",
  "config": "shipgate.yaml",
  "policy": null
}
```

`shipgate.check` output is exactly `agent_result_v1`.

`shipgate.preflight` returns `PreflightResultV2`; prefer the `plan` argument
with a `PreflightPlanV1` object for protected-surface routing, high-risk
capability evidence requests, and host/MCP permission review. `shipgate.explain` returns
deterministic check/finding explanation JSON. `shipgate.capabilities` returns
capability lock or capability lock diff JSON. These are projections only; the
release gate remains `report.json.release_decision.decision`.

The MCP server is a static adapter only. It exposes no scan, verify,
apply-patches, shell, git, network, external MCP connection, or write-capable
tools, and must not be treated as a privileged runtime gate or a general MCP
permission broker.
