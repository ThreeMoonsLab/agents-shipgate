# Agents Shipgate Recipes

Use these recipes after the `agents-shipgate` skill triggers.

## CLI Preflight

The Codex plugin supplies the workflow instructions, not the scanner binary.
Before running `agents-shipgate` commands, confirm the CLI is installed and new
enough for the `verify` workflow:

```bash
command -v agents-shipgate
agents-shipgate --version
agents-shipgate contract --json
```

Require `agents-shipgate contract --json` to report `contract_version: "5"` or
newer. If the command is missing or the contract is older, ask the user to
install or upgrade the CLI and rerun the task:

```bash
pipx install agents-shipgate
pipx upgrade agents-shipgate  # plain install is a no-op over a stale build
```

After installation, run `agents-shipgate --version` and
`agents-shipgate contract --json` again. Do not continue to `detect`, `init`,
`scan`, or `verify` until the CLI exists and reports contract v5 or newer.

A missing or stale binary is a `decision="block"` install action in the
agent-native protocol, not a reason to proceed unverified. Until
`agents-shipgate contract --json` confirms contract v5 or newer, do not report
the task complete: surface the install/upgrade action and stop. Local boundary
checks emit `shipgate.codex_boundary_result/v1`; legacy `agent_result_v1`
fixtures are retained only for older protocol integrations.

## Protected Surface Preflight

Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules,
policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex
plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, run:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate preflight --workspace . --plan - --json
```

Pass a `PreflightPlanV1` object on stdin. If you already have a path list or
local diff and need legacy shorthands, ask preflight about them before editing:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate preflight --workspace . \
  --changed-files changed.txt --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate preflight --workspace . \
  --diff pr.diff --json
```

If `requires_human_review` is true or `first_next_action.actor` is `human`,
stop and route the change to a human. Preflight is a routing surface only;
`release_decision.decision` remains the gate.

## Decide Relevance

Run:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate detect --workspace . --json
```

Proceed when any of these are true:

- `is_agent_project: true`
- `suggested_sources` is non-empty
- `codex_plugin_candidates` is non-empty
- `shipgate.yaml` already exists
- the user explicitly asked for a Shipgate scan or Tool-Use Readiness gate

Stop only when all signals are absent and the user did not explicitly request Shipgate.

## Bootstrap A Repo

Run:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate detect --workspace . --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate contract --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate init --workspace . --write --ci --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate apply-patches \
  --from agents-shipgate-reports/report.json \
  --confidence high --apply
```

If `init` reports placeholders, replace `CHANGE_ME` values from repo context before scanning. If `shipgate.yaml` already exists, edit it rather than overwriting it.

## Verify An Agent-Related Diff

Use this before finishing a PR or local change that touches an agent tool
surface, prompts, policies, permissions, Shipgate CI, or other protected
release surfaces.

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate trigger \
  --workspace . --base origin/main --head HEAD --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate preflight --workspace . --plan - --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify \
  --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

For local uncommitted work, omit `--head` and omit `--base` so `verify` scans
the checked-out working tree, including uncommitted edits. In committed PR or
CI contexts, make the base ref available first because `verify` never fetches.
If you pass a missing `--base`, `verify` exits 2 with an unknown merge verdict.

Read `agents-shipgate-reports/verifier.json` first. Lead with
`merge_verdict`, then inspect `capability_review.top_changes[]`,
`first_next_action.actor`, and `fix_task.safe_to_attempt`. Then read
`agents-shipgate-reports/report.json`; `release_decision.decision` remains the
gate. Use `verifier_summary` only as a composition summary: its `verdict`
mirrors `release_decision.decision` and it adds counts for protected-surface
touches, policy weakening, human acknowledgement, and top reason codes.

Do not bypass the verifier. Do not suppress findings, lower severity, expand
baselines or waivers, remove Shipgate CI, or weaken agent instructions to make
the run pass. Verify-mode `SHIP-VERIFY-*` findings route those trust-root
changes to human review.

## First-Time CI

Use advisory mode only. Copy `assets/advisory-pr-comment.yml` to `.github/workflows/agents-shipgate.yml`.

Do not switch to release-blocking behavior in the same task. Strict promotion requires human review, suppressions with reasons, and optionally a saved baseline.

## Fix Top Finding

1. Read `agents-shipgate-reports/report.json`.
2. Pick the first blocker, then highest-severity review item.
3. If `findings[].agent_action == "auto_apply"` and a high-confidence patch exists, apply it with `apply-patches --confidence high --apply`.
4. For policy/evidence gaps, propose the exact human decision needed. Do not fabricate approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime-trace evidence.
5. Re-run scan and report the new `release_decision.decision`, blocker count, and review item count.

## Recommend Fixes

Group active findings by action:

- `auto_apply`: safe mechanical patches.
- `propose_patch_for_review`: show patch, leave final decision to user.
- `escalate_to_human`: policy/evidence decision.
- `suppress_with_reason`: only when the user confirms the finding is intentionally accepted.
- `informational`: summarize, no gate action.

## Explain A Finding

Run:

```bash
agents-shipgate explain-finding <fingerprint> \
  --from agents-shipgate-reports/report.json --json
```

Use the returned deterministic `explanation` for PR comments or chat replies. Keep it to 3-5 sentences and include the tool name, release risk, and next action.

## Triage False Positives

Prefer fixing the manifest or policy evidence over suppression. Suppress only with a specific reason:

```yaml
checks:
  ignore:
    - check_id: SHIP-CHECK-ID
      tool: tool.name
      reason: specific accepted-risk rationale
```

## Promote Advisory To Strict

Only after humans review advisory output:

```bash
agents-shipgate baseline save -c shipgate.yaml --out .agents-shipgate/baseline.json
agents-shipgate scan -c shipgate.yaml \
  --baseline .agents-shipgate/baseline.json \
  --ci-mode strict --fail-on critical,high
```

The promoted gate should fail only on new findings above the selected threshold.

## Upgrade Shipgate

Update the GitHub Action tag and `shipgate_version` together. Re-run:

```bash
agents-shipgate contract --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
```

If schema or decision fields changed, use `docs/agent-contract-current.md` from the installed version or upstream repo.
