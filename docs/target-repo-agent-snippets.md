# Target-Repo Agent Snippets

Use these snippets in repos that contain tool-using AI agents. They make
Agents Shipgate visible to coding agents working in that target repo, not only
to agents reading the Agents Shipgate source repo.

> The CLI plants these snippets for you. Run
> `agents-shipgate init --write --json` for the minimal preview route. Add CI
> and agent-instruction trust roots as separate reviewed changes. After review,
> `init --write --agent-instructions=default` installs the full default
> downstream kit (`AGENTS.md`, `CLAUDE.md`, Cursor rule, Claude command,
> local contract, advisory CI), or pass an explicit subset such as
> `--agent-instructions=agents-md,codex-skill`. Shared host files use managed
> `<!-- agents-shipgate:start -->` blocks; full-file and skill-bundle targets
> use safe-update checks. Idempotent — safe to rerun. The raw content below is
> the canonical reference and the source the renderers in
> `src/agents_shipgate/cli/discovery/agent_instructions/renderers/` lift from.

## When To Run

Run Agents Shipgate when a repo or PR changes:

- agent tools or tool decorators
- MCP exports
- OpenAPI specs
- prompts that constrain tool use
- permissions, scopes, approval policies, or confirmation policies
- `.github/workflows/agents-shipgate.yml` or another release gate
- `shipgate.yaml`

Skip it for pure docs, tests, formatting, or refactors with no tool or policy
impact unless Shipgate is already configured or the user explicitly asks.

## `AGENTS.md`

````md
## Tool-Use Readiness Release Gate

This repo uses Agents Shipgate as the deterministic merge gate for AI-generated
agent capability changes — a local-first, static Tool-Use Readiness review.

Run Shipgate when this PR changes agent tools, MCP exports, OpenAPI specs,
prompts that constrain tool use, permissions, approval policies,
confirmation policies, CI release gates, or `shipgate.yaml`.

Skip Shipgate for pure docs, tests, formatting, or refactors with no tool or
policy impact unless the user explicitly asks.

Commands:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
shipgate check --agent claude-code --workspace . --format agent-boundary-json
shipgate check --agent cursor --workspace . --format agent-boundary-json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

For local agent control, read the `shipgate check` stdout JSON only. It is
`shipgate.agent_boundary_result/v2`; switch on `control.state`, then follow
`control.next_action`, `control.allowed_next_commands`, and
`control.human_review`. Treat `decision` as diagnostic context, not as the
operational control signal. Do not infer control from prose.

Before finishing an agent-related diff, run `shipgate check`. If
`control.state=complete`, summarize the result and finish. If
`control.state=agent_action_required`, perform only the exact coding-agent
action and command authorized by `control.next_action`, then rerun the command.
If `control.state=review_publishable`, a human must approve the merge — surface
the JSON result and note that you may still commit, push, and update the pull
request so that review can happen. If `control.state=human_review_required`,
stop and surface the JSON result to a human. `control.permissions` states the
authority exactly: updating a pull request is not merging it, and
`permissions.merge` / `permissions.report_complete` are false on every state
except `complete`. Conversation-level acknowledgement never clears these
states; only a new verifier artifact can do so.

For committed PR/CI verification, run `agents-shipgate verify --base
origin/main --head HEAD --json` after making the base ref available; it never
fetches. Validate `agents-shipgate-reports/verification-receipt.json` first,
then read `agents-shipgate-reports/agent-handoff.json` for
`gate.merge_verdict`, `gate.can_merge_without_human`, and `control`; then read
`agents-shipgate-reports/verifier.json` for detailed control context,
`agents-shipgate-reports/verify-run.json` for reproducibility metadata, and
`agents-shipgate-reports/report.json.release_decision.decision` for the
release gate.
Legacy `agent-result.json` surfaces, where present, are supporting/provisional
projections and not the CI gate.

`agents-shipgate-reports/current-control.json` is the one entry point that
says which control identity is current. Read it with `agents-shipgate agent
control --workspace .`, which checks the pointer against the repository as it
stands right now — a moved HEAD, a changed tree, or an edited working file
refuses the read. A non-zero exit means nothing is current here and you hold no
authority. Re-read it after any human or external-tool action, after commit,
rebase, checkout, pull, or any worktree change, after any agents-shipgate
command returns, before enforcing a cached `must_stop`, before commit/push/PR
update, before merge or release, and before declaring the task complete. If
`current_control_id` changed, discard every cached control state and restart
from the new identity. A result you remember from earlier in this conversation
never outranks the current pointer — in either direction.

For coding-agent host grants, run `shipgate audit --host` and read the emitted
host-grants inventory before changing MCP servers, permission rules, hooks, or
workflow scopes.

Auto-apply only high-confidence safe patches. Do not auto-assert action effect,
action authority, approval, confirmation, idempotency, broad-scope, or
prohibited-action policy decisions; surface those as human review items.

Do not bypass the verifier by suppressing findings, lowering severity,
expanding baselines or waivers, removing Shipgate CI, or weakening agent
instructions. Verify-mode `SHIP-VERIFY-*` checks make those trust-root edits
release-visible.

Before committing, ensure `.gitignore` includes:

```gitignore
agents-shipgate-reports/
```
````

## Codex Skill

For OpenAI Codex, generate the repo-scoped skill into
`.agents/skills/agents-shipgate/`:

```bash
agents-shipgate init --workspace . --write --agent-instructions=codex-skill
```

Pair it with the `AGENTS.md` block for the strongest trigger surface:

```bash
agents-shipgate init --workspace . --write --agent-instructions=agents-md,codex-skill
```

The skill can be invoked explicitly with `$agents-shipgate` and may be used
implicitly by Codex when the task matches its frontmatter. It carries a compact
`SKILL.md`, on-demand references for recipes and report reading, and an
advisory GitHub Action template.

## Claude Code Skill

For Claude Code, generate the repo-scoped skill into
`.claude/skills/agents-shipgate/`:

```bash
agents-shipgate init --workspace . --write --agent-instructions=claude-code-skill
```

Pair it with the `AGENTS.md` block and the `CLAUDE.md` managed-block for the
strongest trigger surface:

```bash
agents-shipgate init --workspace . --write \
  --agent-instructions=agents-md,claude-md,claude-code-skill
```

The skill is invoked by typing `/agents-shipgate` in Claude Code, or auto-loaded
when the session is in a repo that matches its frontmatter. It bundles `SKILL.md`,
eight recipe prompts (bootstrap, relevance decision, finding fixes, strict-mode
promotion, false-positive triage, version upgrades, finding explanation), and an
advisory GitHub Action template under `ci-recipes/`.

## `CLAUDE.md`

````md
## Agents Shipgate

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review.

For agent tool-surface or release-policy changes, run:

```bash
shipgate check --agent claude-code --workspace . --format agent-boundary-json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

For local agent control, read the `shipgate check` stdout JSON only. It is
`shipgate.agent_boundary_result/v2`; switch on `control.state`, then follow
`control.next_action`, `control.allowed_next_commands`, and
`control.human_review`. Treat `decision` as diagnostic context, not as the
operational control signal.

Before finishing an agent-related diff, run `shipgate check`. If
`control.state=complete`, summarize the result and finish. If
`control.state=agent_action_required`, perform only the exact coding-agent
action and command authorized by `control.next_action`, then rerun the command.
If `control.state=review_publishable`, a human must approve the merge — surface
the JSON result and note that you may still commit, push, and update the pull
request so that review can happen. If `control.state=human_review_required`,
stop and surface the JSON result to a human. `control.permissions` states the
authority exactly: updating a pull request is not merging it, and
`permissions.merge` / `permissions.report_complete` are false on every state
except `complete`. Conversation-level acknowledgement never clears these
states; only a new verifier artifact can do so.

For committed PR/CI verification, run `agents-shipgate verify --base
origin/main --head HEAD --json` after making the base ref available; it never
fetches. Validate `agents-shipgate-reports/verification-receipt.json` first,
then read `agents-shipgate-reports/agent-handoff.json` for
`gate.merge_verdict`, `gate.can_merge_without_human`, and `control`; then read
`agents-shipgate-reports/verifier.json` for detailed control context,
`agents-shipgate-reports/verify-run.json` for reproducibility metadata, and
`agents-shipgate-reports/report.json.release_decision.decision` for the
release gate.
Legacy `agent-result.json` surfaces, where present, are supporting/provisional
projections and not the CI gate.

`agents-shipgate-reports/current-control.json` is the one entry point that
says which control identity is current. Read it with `agents-shipgate agent
control --workspace .`, which checks the pointer against the repository as it
stands right now — a moved HEAD, a changed tree, or an edited working file
refuses the read. A non-zero exit means nothing is current here and you hold no
authority. Re-read it after any human or external-tool action, after commit,
rebase, checkout, pull, or any worktree change, after any agents-shipgate
command returns, before enforcing a cached `must_stop`, before commit/push/PR
update, before merge or release, and before declaring the task complete. If
`current_control_id` changed, discard every cached control state and restart
from the new identity. A result you remember from earlier in this conversation
never outranks the current pointer — in either direction.

For coding-agent host grants, run `shipgate audit --host` and read the emitted
host-grants inventory before changing MCP servers, permission rules, hooks, or
workflow scopes.

Use `apply-patches --confidence high --apply` only for high-confidence safe
patches. Action effect, action authority, approval, confirmation, idempotency,
broad-scope, and prohibited-action changes require human review.

Do not bypass the verifier by suppressing findings, lowering severity, expanding
baselines or waivers, removing Shipgate CI, or weakening agent instructions.
Verify-mode `SHIP-VERIFY-*` checks make those trust-root edits release-visible.
````

## `.cursor/rules/agents-shipgate.mdc`

```md
---
description: Run Agents Shipgate as the deterministic merge gate for AI-generated agent capability changes.
globs:
  - "shipgate.yaml"
  - "**/*openapi*.yaml"
  - "**/*openapi*.yml"
  - "**/*openapi*.json"
  - "**/*swagger*.yaml"
  - "**/*swagger*.yml"
  - "**/*swagger*.json"
  - "**/*mcp*.json"
  - "**/*tools*.json"
  - ".codex-plugin/**"
  - "**/.codex-plugin/**"
  - ".agents/plugins/**"
  - "**/.agents/plugins/**"
  - "**/.app.json"
  - "**/.mcp.json"
  - "**/SKILL.md"
  - "n8n/*.json"
  - "workflows/*.json"
  - "**/*workflow*.json"
  - ".agents-shipgate/*.json"
  - "prompts/**"
  - "**/prompts/**"
  - "policies/**"
  - "**/policies/**"
  - ".github/workflows/agents-shipgate.yml"
  - ".github/workflows/agents-shipgate.yaml"
alwaysApply: false
---

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review.

When a change affects agent tools, MCP exports, OpenAPI specs, prompts,
permissions, approval policies, or release gates, run Agents Shipgate.
Default to advisory verification while adopting the gate.

For local agent control, run:

  shipgate check --agent cursor --workspace . --format agent-boundary-json

Read the check stdout JSON only. It is
`shipgate.agent_boundary_result/v2`; switch on `control.state`, then follow
`control.next_action`, `control.allowed_next_commands`, and
`control.human_review`. Treat `decision` as diagnostic context, not as the
operational control signal. Do not infer control from prose.

If `control.state=complete`, summarize the result and finish. If
`control.state=agent_action_required`, perform only the exact coding-agent
action and command authorized by `control.next_action`, then rerun the command.
If `control.state=review_publishable`, a human must approve the merge — surface
the JSON result and note that you may still commit, push, and update the pull
request so that review can happen. If `control.state=human_review_required`,
stop and surface the JSON result to a human. `control.permissions` states the
authority exactly: updating a pull request is not merging it, and
`permissions.merge` / `permissions.report_complete` are false on every state
except `complete`. Conversation-level acknowledgement never clears these
states; only a new verifier artifact can do so.

For local verification, run:

  agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json

For committed PR/CI verification, run `agents-shipgate verify --base
origin/main --head HEAD --json` after making the base ref available; it never
fetches. Validate `agents-shipgate-reports/verification-receipt.json` first,
then read `agents-shipgate-reports/agent-handoff.json` for
`gate.merge_verdict`, `gate.can_merge_without_human`, and `control`; then read
`agents-shipgate-reports/verifier.json` for detailed control context,
`agents-shipgate-reports/verify-run.json` for reproducibility metadata, and
`agents-shipgate-reports/report.json.release_decision.decision` for the
release gate.
Legacy `agent-result.json` surfaces, where present, are supporting/provisional
projections and not the CI gate.

`agents-shipgate-reports/current-control.json` is the one entry point that
says which control identity is current. Read it with `agents-shipgate agent
control --workspace .`, which checks the pointer against the repository as it
stands right now — a moved HEAD, a changed tree, or an edited working file
refuses the read. A non-zero exit means nothing is current here and you hold no
authority. Re-read it after any human or external-tool action, after commit,
rebase, checkout, pull, or any worktree change, after any agents-shipgate
command returns, before enforcing a cached `must_stop`, before commit/push/PR
update, before merge or release, and before declaring the task complete. If
`current_control_id` changed, discard every cached control state and restart
from the new identity. A result you remember from earlier in this conversation
never outranks the current pointer — in either direction.

For coding-agent host grants, run:

  shipgate audit --host --json --out agents-shipgate-reports/host-grants.json

Read the host-grants inventory before changing MCP servers, permission rules,
hooks, or workflow scopes.

Apply only high-confidence safe patches. Do not invent action effect, action
authority, approval, confirmation, or idempotency evidence.

Do not bypass the verifier by suppressing findings, lowering severity,
expanding baselines or waivers, removing Shipgate CI, or weakening agent
instructions. Verify-mode `SHIP-VERIFY-*` checks make those trust-root edits
release-visible.

For one-fetch counts and a deterministic next step, read
`report.json.agent_summary` (v0.12+): verdict, blocker_count,
review_item_count, auto_appliable_patches, needs_human_review,
first_recommended_action.

For per-finding routing read `findings[].agent_action` (v0.12+):
auto_apply, propose_patch_for_review, escalate_to_human,
suppress_with_reason, informational. Do not synthesize an action from
the underlying flags when the enum is present.

For reviewer triage by source reliability, run
`agents-shipgate findings --from agents-shipgate-reports/report.json
--provenance-kind keyword_heuristic,regex_heuristic --json`. The
underlying `findings[].provenance_kind` field is a filter signal only,
not a gate input.

To translate a single finding into user-facing prose, run:

  agents-shipgate explain-finding <FINGERPRINT> \
      --from agents-shipgate-reports/report.json --json

The payload includes the full Finding shape plus `metadata` (catalog
CheckMetadata) and `explanation` (a deterministic 3–5 sentence prose
summary). See `prompts/explain-finding-to-user.md` for the
translation rubric.

References:

- AGENTS.md — agent-facing instructions
- docs/agent-contract-current.md — current schema versions and field list
- docs/agent-action-guide.md — per-category recipe for what to DO with a finding
- docs/upstream-integrations.md — per-framework drop-in (60-second adoption)
- docs/triggers.json — machine-readable mirror of the trigger table
```

## `.github/pull_request_template.md`

````md
## Tool-Use Readiness Release Gate

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review.

- [ ] If this PR changes agent tools, MCP/OpenAPI specs, prompts, permissions,
      approval policy, confirmation policy, CI release gates, or
      `shipgate.yaml`, I ran:

      ```bash
      agents-shipgate verify --workspace . --config shipgate.yaml \
        --ci-mode advisory --format json
      ```

- [ ] I reviewed `agents-shipgate-reports/verifier.json`, led with
      `merge_verdict`, and checked `capability_review.top_changes[]`.
- [ ] I reviewed `agents-shipgate-reports/report.json` and used
      `release_decision.decision` as the release gate.
- [ ] I did not auto-assert action effect, action authority, approval,
      confirmation, idempotency, broad-scope, or prohibited-action policy
      decisions.
````

## Advisory GitHub Action

```yaml
name: Agents Shipgate

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  agents-shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v0.15.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
          diff_base: target
          pr_comment: "true"
          shipgate_version: "0.15.0"
```

Advisory mode reports findings without blocking merge. Move to strict mode only
after the team has triaged current findings and saved a baseline.

## Verify-On-PR Instruction

Add this verifier instruction to any agent-facing surface in the target repo
(for example the body of `AGENTS.md` or `CLAUDE.md`). It is the ongoing-PR
companion to the bootstrap snippets above:

```text
Before claiming completion on any PR that changes agent tools, MCP exports,
OpenAPI specs, prompts, permissions, policies, CI gates, or shipgate.yaml, run:
`agents-shipgate verify --base origin/main --head HEAD --json`. Read
agents-shipgate-reports/agent-handoff.json first: gate.merge_verdict,
control.state, gate.can_merge_without_human, next_action, fix_task, and
capability_review.top_changes. Then read report.json.release_decision.decision;
it is the gate. Do not claim completion unless control.state is complete.
Conversation-level acknowledgement cannot clear a human-review route. Never weaken shipgate.yaml, Shipgate CI,
AGENTS.md, skills, policy packs, baselines, waivers, or suppressions merely to
make Shipgate pass.
```

`verifier.json` leads with `merge_verdict`
(`mergeable` / `human_review_required` / `insufficient_evidence` / `blocked` /
`unknown`), a deterministic projection of `release_decision.decision` — the gate,
which lives in `report.json`. `fix_task` is the deterministic repair boundary:
agent-safe mechanical work has `actor: coding_agent`; action effect, action
authority, approval, idempotency, waiver, baseline, and policy authority has
`actor: human`. See
[`use-cases/ai-generated-agent-prs.md`](use-cases/ai-generated-agent-prs.md) for
the full PR-verification walkthrough.
