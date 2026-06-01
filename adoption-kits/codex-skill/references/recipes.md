# Agents Shipgate Recipes

Use these recipes after the `agents-shipgate` skill triggers.

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
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify \
  --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json
```

For local pre-commit work, omit `--head` and omit `--base` unless the base ref
exists locally so `verify` scans the checked-out working tree, including
uncommitted edits. In committed PR or CI contexts, add
`--base origin/main --head HEAD` after making the base ref available. If you
pass a missing `--base`, `verify` exits 2 with an unknown merge verdict.

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
