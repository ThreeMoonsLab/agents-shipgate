# Agents Shipgate Agent-Native Protocol

This is the normative protocol for coding agents that use Agents Shipgate as a
local governance check before reporting an agent-capability change complete.

Agents only need one command and one JSON schema:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
```

Use `--agent claude-code` for Claude Code and `--agent cursor` for Cursor.
This value identifies the calling agent and changes only actor/rerun metadata;
it never selects or disables host coverage. Every recognized changed boundary
surface is evaluated on every invocation.
The command writes no repo artifacts by default. It prints one JSON object to
stdout: `shipgate.agent_boundary_result/v2`.

`agents-shipgate verify` and `agents-shipgate-reports/report.json` remain the
full CI and reviewer substrate. Coding agents should use them for committed PR
verification and reviewer evidence, but their local control loop is
`shipgate check` plus `shipgate.agent_boundary_result/v2`.

## Command

Default local check:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
```

Committed diff check:

```bash
shipgate check --agent codex --workspace . --base origin/main --head HEAD --format agent-boundary-json
```

Fixture or MCP-provided diff:

```bash
shipgate check --agent codex --workspace . --diff change.diff --format agent-boundary-json
shipgate check --agent codex --workspace . --diff - --format agent-boundary-json
```

A supplied file or stdin diff is detached diagnostic input: it is not bound
to checkout bytes that `verify` can reconstruct. Shipgate can still report
boundary findings from it, but when the result owes verification it returns
`human_review_required` with no allowed command. Re-run the check against the
intended worktree, or with both `--base` and `--head`, to obtain a replayable
verification route.

The no-`--diff` form resolves a git diff locally. With no `--base` or `--head`,
it reads local staged, unstaged, deleted, renamed, and relevant untracked
changes. With `--base` and `--head`, it reads
`base...head`. Supplying only one of `--base` or `--head` is invalid; omit both
for local work or provide both for committed refs. Shipgate never fetches refs.

## Result Schema

The stdout object has:

- `schema_version: "shipgate.agent_boundary_result/v2"`
- `actor: "codex" | "claude-code" | "cursor"`
- `input_mode` and `scope`
- `input_coverage`
- `host_coverage[]` and `affected_hosts[]`
- `policies[]` and `policy_set_sha256`
- `issues[]` and `excluded_scopes[]`
- `static_analysis_only: true`
- `runtime_session_verified: false`
- `decision: "allow" | "warn" | "block" | "require_review"`
- `violations[]` and `violated_rules[]` (identical projections of the same
  evaluated rule set)
- `pending_review[]` — review obligations the graded local mapping carries
  forward instead of stopping the turn. Non-empty only alongside
  `agent_action_required`; each entry has `check_id`, `rule_id`, `path`,
  `risk_level`, `title`, `reviewers`, and `note`. Report these items when
  summarizing the change; PR-time verify routes them to a human reviewer.
- `control.state: "complete" | "agent_action_required" | "review_publishable" | "human_review_required"`
- `control.reason`
- `control.completion_allowed`
- `control.must_stop`
- `control.verify_required`
- `control.next_action`
- `control.allowed_next_commands`
- `control.permissions` — the exact booleans `edit`, `commit`, `push`,
  `update_pr`, `merge`, `report_complete`. Fixed by `control.state` and by the
  route in `control.next_action`; never an independent switch. Updating a pull
  request is not merging it. A `fetch_base` or `install` route authorizes none
  of the six: nothing has been evaluated yet, so only `next_action` is
  authorized. Absent on a pre-contract-20 artifact — reconstruct it from the
  state and route, never assume publication.
- `control.human_review`
- `repair`
- `policies[]`
- `source_artifacts`
- `audit_id`

Consumers must make decisions from JSON fields, never from prose or Markdown.
The stable schema is `docs/agent-boundary-result-schema.v2.json`. Operational
consumers switch only on `control.state`; `decision` is diagnostic context.
`control.completion_allowed` is true exactly for `complete`, and
`control.must_stop` is true
exactly for `human_review_required`. `control.permissions.merge` and
`control.permissions.report_complete` always equal
`control.completion_allowed`, and `control.must_stop=true` authorizes nothing
at all. The converse does not hold: `must_stop=false` is not a promise that
publication is authorized — read `control.permissions`. `risk_level` remains explanatory.

With `--format agent-boundary-json`, schema-valid results exit `0`; wrappers
must switch on `control.state`, not `$?`. A missing input may return an
`agent_action_required` `fetch_base` action, which carries no `command` and
names the input in `expects` instead: the exact refs to make available, or —
from `verify --preview`, whose project markers are read from the working tree —
the commit this worktree must have checked out. `expects` names a commit id
rather than the ref you passed, because the checkout it asks for moves `HEAD`
and a revision expression would then mean something else.
An unreadable diff file, detached stdin/file diff that owes verification, or
worktree/ref state Shipgate cannot bind returns `human_review_required` and
authorizes no speculative rerun. Unsupported CLI shape errors such as an
invalid `--agent` or `--format` still exit nonzero before a boundary-result
object exists.

## State Machine

| `control.state` | Agent action |
|---|---|
| `complete` | Completion is allowed. Summarize warnings, if any. No mandatory action remains. |
| `agent_action_required` | Do not claim completion. Perform only the exact coding-agent route in `control.next_action`, then rerun. If `pending_review[]` is non-empty, also name those items when you summarize the change — the obligation travels with the PR, not with the turn. |
| `review_publishable` | Do not merge and do not claim completion. A human must approve the merge, and you may still publish the change for that review: commit, push, and update the pull request. Surface `control.human_review.why` and, if `allowed_next_commands` names one, rerun it after publishing so the evidence matches the committed refs. |
| `human_review_required` | Stop all coding-agent action and surface `control.reason` plus the human next action. |

`review_publishable` and `human_review_required` both require a person. They
differ only in what stays authorized meanwhile, which `control.permissions`
states exactly. `human_review_required` is reserved for results Shipgate cannot
vouch for, and publication requires *every* one of these to hold:

- a subject `verify` can replay — a diff supplied through `--diff`, stdin, or
  the MCP tool is detached from checkout bytes and never qualifies;
- input the evaluator read in full — `BOUNDARY-INPUT-INCOMPLETE`, unresolved
  parse failures, and experimental adapters all deny it;
- a decision that is not `block`.

Anything else keeps the total stop.

`control.must_stop=true` is reserved for the stopping human route.
Installation, repair, discovery, configuration, fetch-base, rerun, and graded
review (a
`require_review` set that is entirely low/medium risk, routed to verify with
its obligations in `pending_review[]`) are `agent_action_required`, never stop
states. Graded review is the one `agent_action_required` shape that carries an
unresolved *human* obligation: the agent may finish its work, and PR-time
verify still routes the change to a reviewer. Conversation-level human
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
route to a person: `control.state="review_publishable"` when the boundary was
evaluated and only judgement is outstanding, `control.state="human_review_required"`
for a `block` decision or input Shipgate could not bind. Neither authorizes
merge or completion, and neither is agent-repairable.

`repair.forbidden_shortcuts` is present on every result, including `complete`, so
agents have the same trust-root boundary even when no finding fires.

## Coverage

`shipgate check` is repository-boundary-scoped: it evaluates Codex, Claude
Code, Cursor, experimental VS Code MCP, shared instruction, Shipgate trust-root,
and GitHub workflow surfaces from the diff. A complete result requires every
registered host adapter to be either evaluated or proven inapplicable; malformed,
unreadable, oversized, external, symlinked, and otherwise unresolved relevant
inputs cannot produce `control.state="complete"`. It does **not** compute the tool-use
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

- `control.next_action.actor="human"` means a person must decide.
- `required_reviewers[]` names reviewer roles.
- `control.state="review_publishable"` means that person decides the *merge*.
  Publishing the change for them to look at is still authorized.
- `control.state="human_review_required"` means the agent must stop outright.
- `control.must_stop=true` means the agent cannot take further tool action.
- `control.permissions.merge=false` and
  `control.permissions.report_complete=false` are what a human review actually
  denies. Never treat "I updated the PR" as "the gate let this through".

Do not bypass Shipgate by suppressing findings, lowering severity, expanding a
baseline, adding a waiver, removing CI, weakening agent instructions, or editing
Shipgate policy to pass. Those edits are trust-root changes and must block or
route to human review.

## Policy Discovery

Policy discovery is deterministic:

1. `--policy <path>` wins.
2. Then `policies/agent-boundary.shipgate.yaml` in the workspace.
3. Then legacy Codex and host policy files, limited to their original rule
   families.
4. Then the packaged unified default for missing families.

Coexisting unified and legacy workspace policies, duplicate inconsistent rule
definitions, invalid explicit policy, and unknown explicit policy fields fail
closed. Legacy policy discovery is deprecated through `0.16.x` and never
auto-migrated.

Every result includes:

- `policies[].source`
- `policies[].id`
- `policies[].version`
- `policies[].snapshot_sha256`
- `policies[].discovery[]`
- `policy_set_sha256`

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
  "schema_version": "shipgate.agent_boundary_result/v2",
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

A binary that is present but older than runtime contract 20 is the other
fail-safe case: a stale copy lingering on `PATH` can emit an outdated schema or
lack the command this protocol expects (a plain `pipx install` is a no-op over
an already-installed older build). Confirm the version first with
`agents-shipgate --version` and `agents-shipgate contract --json`; if the
contract is older than required, do not trust the stale binary's output.
Surface a schema-valid boundary-result object that routes to an upgrade:

```json
{
  "schema_version": "shipgate.agent_boundary_result/v2",
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

`shipgate.check` output is exactly `shipgate.agent_boundary_result/v2`.

`shipgate.preflight` returns `PreflightResultV3`; prefer the `plan` argument
with a `PreflightPlanV1` object for protected-surface routing, high-risk
capability evidence requests, and host/MCP permission review. `shipgate.explain` returns
deterministic check/finding explanation JSON. `shipgate.capabilities` returns
capability lock or capability lock diff JSON. `shipgate.handoff` reads existing
`verifier.json` / `report.json` / `verify-run.json` artifacts and returns exact
`shipgate.agent_handoff/v7`. These are projections only; the
release gate remains `report.json.release_decision.decision`.

The MCP server is a static adapter only. It exposes no scan, verify,
apply-patches, shell, git, network, external MCP connection, or write-capable
tools, and must not be treated as a privileged runtime gate or a general MCP
permission broker.
