# Agents Shipgate Recipes

Use these recipes after the `agents-shipgate` skill triggers. The prominent
flows are `shipgate check`, `agents-shipgate verify`, and `shipgate audit --host`.
Supporting commands remain callable, but should not be the first thing an agent
recommends.

## CLI Preflight

The Codex plugin supplies workflow instructions, not the scanner binary.
Before running Shipgate commands, confirm the CLI is installed and new enough:

```bash
command -v agents-shipgate
agents-shipgate --version
agents-shipgate contract --json
```

Require `agents-shipgate contract --json` to report
`minimum_control_contract_version: 14`. If it is missing or stale, ask the
user to install or upgrade:

```bash
pipx install agents-shipgate
pipx upgrade agents-shipgate
```

Do not report the task complete until the CLI exists and reports runtime
contract 14. Local boundary checks emit `shipgate.agent_boundary_result/v1`;
legacy v1 fixtures are retained only for older protocol integrations.

## Local Agent Check

Run the boundary check before reporting an agent-related local diff complete:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 shipgate check \
  --agent codex --workspace . --format agent-boundary-json
```

Read only stdout JSON. Switch on `control.state`, follow
`control.next_action`, `control.allowed_next_commands`, and
`control.human_review`, and treat `decision` as diagnostic context only.

## Verify A Diff

Use this before finishing a PR or local change that touches an agent tool
surface, prompts, policies, permissions, Shipgate CI, or other protected
release surfaces.

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify \
  --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

For local uncommitted work, omit `--head` and `--base` so `verify` scans the
checked-out working tree, including uncommitted edits. In committed PR or CI
contexts, make the base ref available first because `verify` never fetches. If
the repo is not configured or relevance is unclear, run:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify --preview --json
```

Read `agents-shipgate-reports/agent-handoff.json` first. Lead with
`control.state`, then inspect `gate.merge_verdict`, `next_action`,
`fix_task.safe_to_attempt`, and `capability_review.top_changes[]`. Then read
`verifier.json`, `verify-run.json`, and `report.json`; the release gate remains
`report.json.release_decision.decision`.

Do not bypass the verifier by suppressing findings, lowering severity,
expanding baselines or waivers, removing Shipgate CI, or weakening agent
instructions. Verify-mode `SHIP-VERIFY-*` findings route those trust-root
changes to human review.

## Audit Host Grants

Run host audit when the task touches MCP servers, permission rules, hooks,
workflow scopes, or coding-agent host configuration:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 shipgate audit --host --json \
  --out agents-shipgate-reports/host-grants.json
```

For drift checks against an acknowledged baseline, use the same flow with
`--drift` and optionally `--fail-on-drift`.

## Supporting Setup And Repair

If `agents-shipgate verify --preview --json` says the repo needs configuration, the
supporting setup commands remain available:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate init --workspace . --write --ci --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate apply-patches \
  --from agents-shipgate-reports/report.json \
  --confidence high --apply
```

If `init` reports placeholders, replace `CHANGE_ME` values from repo context
before verification. If `shipgate.yaml` already exists, edit it rather than
overwriting it.

## Fix Or Explain Findings

1. Read `agents-shipgate-reports/report.json`.
2. Pick the first blocker, then highest-severity review item.
3. Auto-apply only high-confidence safe patches.
4. For policy/evidence gaps, propose the exact human decision needed. Do not
   fabricate action effect, action authority, approval, confirmation,
   idempotency, broad-scope,
   prohibited-action, or runtime-trace evidence.
5. Re-run `agents-shipgate verify` and report the new merge verdict, release
   decision, blocker count, and review-item count.

For one finding:

```bash
agents-shipgate explain-finding <fingerprint> \
  --from agents-shipgate-reports/report.json --json
```

Suppressions require a specific non-empty reason and explicit user approval.
