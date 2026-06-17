# Verify an AI-Generated Agent PR

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review. Built for
AI coding workflows: when Claude Code, Codex, Cursor, or a human changes an
agent's tool access, Agents Shipgate turns the diff into a deterministic merge
verdict.

This page is the end-to-end use case for that workflow. For the deeper
engineering rationale and roadmap, see
[`engineering/ai-coding-workflow-verifier.md`](../engineering/ai-coding-workflow-verifier.md).
For the stability contract of the verify command and `verifier.json`, see
[STABILITY.md §Verify Orchestrator](../../STABILITY.md#verify-orchestrator). For
the exact fields to read, see
[`agent-contract-current.md`](../agent-contract-current.md); this page links
there rather than restating field lists.

## 1. The problem

Coding agents write capability changes faster than humans can review them. A
single prompt — "add a refund tool", "make CI green", "wire up an MCP export" —
can expand what an AI agent is allowed to do in production. Code review catches
code. Eval suites catch behavior. Neither reliably answers the release question:
*given the tool surface this PR declares, do we have approval policies, scope
coverage, idempotency evidence, and review readiness for every action?*

Worse, an optimizer told to "make the gate pass" may edit the gate instead of
fixing the underlying readiness issue. The reviewer needs a deterministic check
that a coding agent cannot satisfy by rewriting policy, arguing with the output,
or quietly weakening the gate.

## 2. What Shipgate checks

`agents-shipgate verify` runs the same static, no-network, no-LLM scan as
`agents-shipgate scan`, plus diff context derived from the PR's base and head.
It reasons about three things:

- **Tool and action surface.** Which tools, actions, scopes, and effects the PR
  adds, removes, or modifies — across MCP, OpenAPI, OpenAI Agents SDK, Anthropic
  Messages API, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API artifacts,
  Codex plugins, and n8n. See the [check catalog](../checks.md).
- **Approval, confirmation, idempotency, and scope evidence.** Whether a
  money-moving or externally-visible action ships with a declared approval
  policy, a confirmation policy, idempotency evidence, and bounded scope.
- **Trust roots.** Whether the PR touched a release-gate trust root —
  `shipgate.yaml`, the Shipgate CI workflow, `AGENTS.md`/`CLAUDE.md`, policy
  packs, prompts, baselines, waivers, the Cursor rule, or the slash command.
  This is backed by the deterministic `SHIP-VERIFY-TRUST-ROOT-TOUCHED` check and
  surfaced as `trust_root_touched` in `verifier.json`.

The release gate is always `release_decision.decision` in
`agents-shipgate-reports/report.json`. Everything `verify` adds is either an
input to that one decision engine (an ordinary finding) or a deterministic
projection of it. There is no second verdict.

## 3. Example: Codex adds a refund tool → blocked

A coding agent is asked to "add a support-agent feature that can issue Stripe
refunds." It adds `stripe.create_refund` to the tool surface and opens a PR.

`agents-shipgate verify --base origin/main --head HEAD --format json` produces:

- a `capability_review.top_changes[]` row for `stripe.create_refund`, with an
  impact derived from the tool/action surface diff;
- findings that `stripe.create_refund` lacks a declared approval policy and lacks
  idempotency evidence, both `blocks_release: true`;
- `merge_verdict: blocked` (projected from `release_decision.decision: blocked`);
- `can_merge_without_human: false`;
- a `first_next_action` naming a **human** actor: a business owner must decide
  whether refunds require approval above a threshold.

The coding agent cannot resolve this by editing the report or asserting approval
evidence. Approval and idempotency are authority-bearing decisions (see §8).

## 4. Example: an agent edits `shipgate.yaml` to make CI green → human review

A coding agent is told to "make CI green." Instead of fixing the underlying
readiness issue, its patch removes a blocker by editing `shipgate.yaml`.

`verify` classifies `shipgate.yaml` as a trust root via
`SHIP-VERIFY-TRUST-ROOT-TOUCHED`. The result:

- `trust_root_touched: true`;
- a `capability_review.top_changes[]` row for the Shipgate policy change;
- a review-required finding, so `merge_verdict` is at best
  `human_review_required` and `can_merge_without_human` is `false`;
- `first_next_action` names a **human**: a reviewer must confirm the policy
  change is intentional.

Touching a release-gate trust root requires at least human review. The attempt to
weaken the gate becomes a visible, release-relevant signal rather than a silent
pass. `v0.12.0` includes both path-level trust-root detection and semantic
weakening checks over the normalized effective policy: `ci.mode` downgrades,
loosened `fail_on`, suppression/waiver/baseline expansion, CI gate removal,
agent-instruction edits, and trigger catalog drift route to human review or
block release through ordinary `SHIP-VERIFY-*` findings.

## 5. Adoption commands

```bash
pipx install agents-shipgate
agents-shipgate verify --preview --json
agents-shipgate init --workspace . --write --ci --agent-instructions=default --json
agents-shipgate verify --base origin/main --head HEAD --format json
```

- `verify --preview --json` is a lightweight relevance check — no scan, no
  manifest required, exits 0. It emits `mode: "preview"` and a `first_next_action`
  with an exact init command for unconfigured repos or an exact verify command
  for configured repos. Use it as the first touch on any repo or PR.
- `init --write --ci --agent-instructions=default --json` writes
  `shipgate.yaml`, the advisory CI workflow, and the default agent surfaces
  (`AGENTS.md`, the Cursor rule, the Claude `/shipgate` command, and
  `.shipgate/agent-contract.json`). Skill bundles stay explicit targets such as
  `codex-skill`.
- `verify --base origin/main --head HEAD --format json` runs the authoritative head
  scan with diff context and writes the verifier artifacts. `verify` never
  fetches, so make the base ref available first (`fetch-depth: 0` in CI, or
  `git fetch origin main` locally); if the requested diff cannot be inspected,
  verify emits `merge_verdict: unknown` and exits 2 instead of guessing.

To evaluate just the run/skip trigger for a diff:

```bash
agents-shipgate trigger --base origin/main --head HEAD --json
```

## 6. GitHub Action

The action delegates to `agents-shipgate verify` and exposes the verifier
outputs. Drop this into `.github/workflows/agents-shipgate.yml`:

```yaml
name: Agents Shipgate (verify)

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  shipgate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
        with:
          fetch-depth: 0
      - id: shipgate
        uses: ThreeMoonsLab/agents-shipgate@v0.13.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
          diff_base: target
          pr_comment: 'true'
          shipgate_version: '0.13.0'
      - name: Gate on the merge verdict
        run: |
          echo "merge_verdict=${{ steps.shipgate.outputs.merge_verdict }}"
          echo "can_merge_without_human=${{ steps.shipgate.outputs.can_merge_without_human }}"
          echo "trust_root_touched=${{ steps.shipgate.outputs.trust_root_touched }}"
          echo "capability_changes_added=${{ steps.shipgate.outputs.capability_changes_added }}"
```

Key verifier outputs (all `v0.22+`):

- `merge_verdict` — `mergeable` / `human_review_required` /
  `insufficient_evidence` / `blocked` / `unknown`.
- `can_merge_without_human` — `true` / `false`.
- `trust_root_touched` — `true` when the PR changed a release-gate trust root.
- `capability_changes_added`, `capability_changes_modified`,
  `capability_changes_removed` — counts of added/modified/removed agent
  capabilities in the diff.

The original gating outputs (`decision`, `blocker_count`, `review_item_count`,
`ci_would_fail`) remain available. `decision` is still the source of truth;
`merge_verdict` is its projection. Keep `ci_mode: advisory` until your team has
reviewed the output, then switch to `ci_mode: strict`.

## 7. How to read `verifier.json`

Read `agents-shipgate-reports/verifier.json` in this order:

1. **`merge_verdict`** — the headline. One of `mergeable`,
   `human_review_required`, `insufficient_evidence`, `blocked`, or `unknown`. It
   is a deterministic projection of `release_decision.decision`
   (`passed`→`mergeable`, `review_required`→`human_review_required`,
   `insufficient_evidence`→`insufficient_evidence`, `blocked`→`blocked`, missing
   decision→`unknown`). Also read `can_merge_without_human`, `headline`, and
   `human_review.{required, why}`, plus `first_next_action.{actor, kind, command,
   why}` — the actor distinguishes coding-agent work from human-only work.
2. **`capability_review`** — what tool/action access changed, before any generic
   finding. It carries `trust_root_touched`, `policy_weakened`, capability
   change counts, and `top_changes[]` rows with `{id, title, impact, rationale,
   related_finding_ids}`. Also check the underlying `capability_change` block
   when you need the full grouped diff.
3. **`release_decision`** — the full embedded release decision. Its `decision`
   field is the gate; `merge_verdict` cannot disagree with it. For the complete
   field index (blockers, review items, contribution rules, fail policy), see
   [`agent-contract-current.md`](../agent-contract-current.md).

`verifier.json` also carries `trigger` (the run/skip evaluation), `base_status`
/ `head_status` / `changed_files`, `mode`, and an `artifacts` map. Use
`base_status` to understand whether diff enrichment ran — never as a release
verdict. The full schema is
[`docs/verifier-schema.v0.1.json`](../verifier-schema.v0.1.json)
(`verifier_schema_version: "0.1"`).

After `verifier.json`, read `agents-shipgate-reports/report.json` for the full
finding detail. The human PR surface is `agents-shipgate-reports/pr-comment.md`.

## 8. What coding agents may fix vs. what requires humans

Coding agents may perform **mechanical readiness fixes**:

- remove stale manifest entries;
- wire existing declared evidence;
- add missing static metadata when the repository already supports it;
- apply high-confidence safe patches (`apply-patches --confidence high --apply`);
- fix schema or path mistakes.

Coding agents must **not** invent authority-bearing evidence:

- approval, confirmation, or idempotency evidence;
- prohibited-action or broad-scope justification;
- runtime trace evidence;
- business-owner acceptance or human acknowledgement of policy weakening.

When a capability change requires authority, `first_next_action.actor` is
`human` and the change must be reviewed by a person. A coding agent must not
claim completion when `merge_verdict` is `blocked`, `insufficient_evidence`, or
`human_review_required` unless the user has explicitly accepted the human-review
requirement. And a coding agent must **never** weaken `shipgate.yaml`, Shipgate
CI, `AGENTS.md`, policy packs, baselines, waivers, or suppressions merely to make
Shipgate pass — doing so is itself a trust-root change that the gate will flag.

For the full mechanical-vs-human boundary, see
[`agent-autofix-boundary.md`](../agent-autofix-boundary.md). For per-agent verify
recipes, see
[`agents/use-with-claude-code.md`](../agents/use-with-claude-code.md),
[`agents/use-with-codex.md`](../agents/use-with-codex.md), and
[`agents/use-with-cursor.md`](../agents/use-with-cursor.md).
