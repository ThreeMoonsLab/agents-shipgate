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
The stable schema is `docs/agent-result-schema.v1.json`.

## State Machine

| `decision` | Agent action |
|---|---|
| `allow` | Continue. Completion is allowed. |
| `warn` | Continue, but surface the warning in the final task summary. |
| `block` with `first_next_action.actor="coding_agent"` and `repair.safe_to_attempt=true` | Apply only the listed repair, then rerun the exact command in `repair.command` or `first_next_action.command`. |
| `block` with `first_next_action.actor="human"` | Stop. Do not continue, suppress, waive, or weaken policy. |
| `require_review` | Stop and ask for human review. |

`must_stop=true` is an explicit stop boundary. An agent must not claim the task
complete when `must_stop=true`, except to report that human review or install is
required.

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
fixture. Once installed, all other errors must come from Shipgate JSON rather
than agent-authored prose.

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
one tool:

```text
shipgate.check
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

Output is exactly `agent_result_v1`.

The MCP server is a static adapter only. It exposes no scan, verify,
apply-patches, shell, git, network, or write-capable tools, and must not be
treated as a privileged runtime gate.
