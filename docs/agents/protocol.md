# Agents Shipgate Agent-Native Protocol

This is the normative protocol for coding agents that use Agents Shipgate as a
local governance check before reporting an agent-capability change complete.

Agents only need one command and one JSON schema:

```bash
shipgate check --agent codex --workspace . --format codex-boundary-json
```

Use `--agent claude-code` for Claude Code and `--agent cursor` for Cursor.
The command writes no repo artifacts by default. It prints one JSON object to
stdout: `shipgate.codex_boundary_result/v2`.

`agents-shipgate verify` and `agents-shipgate-reports/report.json` remain the
full CI and reviewer substrate. Coding agents should use them for committed PR
verification and reviewer evidence, but their local control loop is
`shipgate check` plus `shipgate.codex_boundary_result/v2`.

## Command

Default local check:

```bash
shipgate check --agent codex --workspace . --format codex-boundary-json
```

Committed diff check:

```bash
shipgate check --agent codex --workspace . --base origin/main --head HEAD --format codex-boundary-json
```

Fixture or MCP-provided diff:

```bash
shipgate check --agent codex --workspace . --diff change.diff --format codex-boundary-json
shipgate check --agent codex --workspace . --diff - --format codex-boundary-json
```

The no-`--diff` form resolves a git diff locally. With no `--base` or `--head`,
it reads local uncommitted tracked changes. With `--base` and `--head`, it reads
`base...head`. Supplying only one of `--base` or `--head` is invalid; omit both
for local work or provide both for committed refs. Shipgate never fetches refs.

## Result Schema

The stdout object has:

- `schema_version: "shipgate.codex_boundary_result/v2"`
- `agent: "codex" | "claude-code" | "cursor"`
- `decision: "allow" | "warn" | "block" | "require_review"`
- `control.state: "complete" | "agent_action_required" | "human_review_required"`
- `control.reason`
- `control.completion_allowed`
- `control.must_stop`
- `control.verify_required`
- `control.next_action`
- `control.allowed_next_commands`
- `control.human_review`
- `repair`
- `policy`
- `source_artifacts`
- `audit_id`

Consumers must make decisions from JSON fields, never from prose or Markdown.
The stable schema is `docs/codex-boundary-result-schema.v2.json`. Operational
consumers switch only on `control.state`; `decision` is diagnostic context.
`control.completion_allowed` is true exactly for `complete`, and
`control.must_stop` is true
exactly for `human_review_required`. `risk_level` remains explanatory.

With `--format codex-boundary-json`, schema-valid results exit `0`; wrappers
must switch on `control.state`, not `$?`. Diff-input recovery is represented as
`agent_action_required` with an exact next action. Unsupported
CLI shape errors such as an invalid `--agent` or `--format` still exit nonzero
before a boundary-result object exists.

## State Machine

| `control.state` | Agent action |
|---|---|
| `complete` | Completion is allowed. Summarize warnings, if any. No mandatory action remains. |
| `agent_action_required` | Do not claim completion. Perform only the exact coding-agent route in `control.next_action`, then rerun. |
| `human_review_required` | Stop all coding-agent action and surface `control.reason` plus the human next action. |

`control.must_stop=true` is reserved for a human route. Installation, repair,
discovery, configuration, fetch-base, and rerun work are
`agent_action_required`, never stop states. Conversation-level human
acknowledgement never changes control state; only a newly generated verifier
artifact can clear it.

The `kind="install"` action is distinct from the repair loop below: it does not
fix a finding, it restores a working gate. It routes to the coding agent while
completion remains false. See [Missing Install](#missing-install) and
[Stale Install](#stale-install) for the two cases and their fixtures.
Consumers identify this route with the exact token
`control.next_action.kind="install"`.

## Repair Loop

Agents may repair only when all of these are true:

- `control.state="agent_action_required"`
- `control.next_action.actor="coding_agent"`
- `control.next_action.kind="repair"`
- `repair.safe_to_attempt=true`
- the repair does not violate `repair.forbidden_shortcuts`

Every agent-safe repair must include a rerun command. After applying the
repair, run that command and parse the next boundary-result object. Completion is
allowed only after a rerun returns `control.state="complete"`.

Human-only authority gaps are never agent-repairable. Approval, confirmation,
idempotency, broad-scope, prohibited-action, waiver, baseline, suppression,
severity downgrade, policy-pack, trace-evidence, and release-policy decisions
must set `control.state="human_review_required"` and stop the agent.

`repair.forbidden_shortcuts` is present on every result, including `complete`, so
agents have the same trust-root boundary even when no finding fires.

## Coverage

`shipgate check` is boundary-scoped: it evaluates host and trust-root surfaces
(Codex/host config, MCP approvals, the Shipgate CI gate, agent instructions,
policy, and skills) from the diff. It does **not** compute the tool-use
capability delta — that is `verify`'s job, and `release_decision.decision`
remains the one authoritative capability gate.

Treat `check` as necessary but not sufficient for capability-expanding diffs.
If a change adds dynamic, undeclared, or otherwise ambiguous tool capability,
`control.state` is `agent_action_required`; run `verify` and read
`release_decision.decision`.

So that `check` never disagrees with that gate, a clean boundary result over a
diff that changes a **manifest-declared tool source** (a `tool_sources[].path`
entry — the changed file equals it, or sits under it when the path is a
scanned directory like an `openai_agents_sdk` agents folder) returns
`control.state="agent_action_required"` with
`control.next_action.kind="verify"`, plus a
`diagnostics[].code="capability_change_requires_verify"` marker and a
`trace[].step="coverage"` event. Completion is not allowed until verification
produces a fresh complete artifact. This keeps `check` from green-lighting a
capability change it did not evaluate. In an adopted repository,
`trigger.force_run=true` requires verify even for docs-only changes.

## Human Boundary

The human approval boundary is explicit:

- `control.state="human_review_required"` means a person must decide.
- `required_reviewers[]` names reviewer roles.
- `control.next_action.actor="human"` means the coding agent must stop.
- `control.must_stop=true` means the agent cannot take further tool action.

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
must surface a schema-valid boundary-result object. Its routing fields must
look like:

```json
{
  "schema_version": "shipgate.codex_boundary_result/v2",
  "decision": "block",
  "control": {
    "state": "agent_action_required",
    "completion_allowed": false,
    "must_stop": false,
    "verify_required": true,
    "next_action": {
      "actor": "coding_agent",
      "kind": "install",
      "command": "pipx install agents-shipgate"
    }
  }
}
```

Use `examples/agent-protocol/expected/missing-install.json` as the full
fixture. Once a current version is installed, all other errors must come from
Shipgate JSON rather than agent-authored prose.

## Stale Install

A binary that is present but older than runtime contract 14 is the other
fail-safe case: a stale copy lingering on `PATH` can emit an outdated schema or
lack the command this protocol expects (a plain `pipx install` is a no-op over
an already-installed older build). Confirm the version first with
`agents-shipgate --version` and `agents-shipgate contract --json`; if the
contract is older than required, do not trust the stale binary's output.
Surface a schema-valid boundary-result object that routes to an upgrade:

```json
{
  "schema_version": "shipgate.codex_boundary_result/v2",
  "decision": "block",
  "control": {
    "state": "agent_action_required",
    "completion_allowed": false,
    "must_stop": false,
    "verify_required": true,
    "next_action": {
      "actor": "coding_agent",
      "kind": "install",
      "command": "pipx upgrade agents-shipgate"
    }
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
legacy `agent_result_v1` module import. It is diagnostic only; it is not a replacement
for `shipgate check` on the active diff.

## Optional MCP Tool

The optional extra `agents-shipgate[mcp]` exposes a read-only MCP server with
static projection tools:

```text
shipgate.check
shipgate.preflight
shipgate.explain
shipgate.capabilities
shipgate.handoff
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

`shipgate.check` output is exactly `shipgate.codex_boundary_result/v2`.

`shipgate.preflight` returns `PreflightResultV3`; prefer the `plan` argument
with a `PreflightPlanV1` object for protected-surface routing, high-risk
capability evidence requests, and host/MCP permission review. `shipgate.explain` returns
deterministic check/finding explanation JSON. `shipgate.capabilities` returns
capability lock or capability lock diff JSON. `shipgate.handoff` reads existing
`verifier.json` / `report.json` / `verify-run.json` artifacts and returns exact
`shipgate.agent_handoff/v3`. These are projections only; the
release gate remains `report.json.release_decision.decision`.

The MCP server is a static adapter only. It exposes no scan, verify,
apply-patches, shell, git, network, external MCP connection, or write-capable
tools, and must not be treated as a privileged runtime gate or a general MCP
permission broker.
