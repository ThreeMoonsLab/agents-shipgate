# Use Agents Shipgate with Claude Code

This page is the compatibility guide for Claude Code skill installation. For
the normative agent protocol, use [claude-code.md](claude-code.md) and
[protocol.md](protocol.md). The canonical Claude Code control command is:

```bash
shipgate check --agent claude-code --workspace . --format agent-json
```

Two pieces of agent-facing surface ship with this repo. Drop them into your own agent project so Claude Code can install, run, and explain Shipgate without you typing the steps.

| Surface | What it does | Source path in this repo |
|---|---|---|
| `/shipgate` slash command | Bootstrap flow: install → `init --write` → fill placeholders → `scan` → report top findings | [`.claude/commands/shipgate.md`](../../.claude/commands/shipgate.md) |
| `agents-shipgate` skill | Auto-discovered when the user mentions release readiness, scanning an agent, fixing a finding, adding Shipgate to CI, or `shipgate.yaml`. Routes to bundled recipes. | [`skills/agents-shipgate/SKILL.md`](../../skills/agents-shipgate/SKILL.md) |

The skill is named `agents-shipgate`, not `shipgate`, on purpose: Claude Code lets a skill with the same name as a command preempt it, which would silently bypass the `/shipgate` slash command. Keeping the names distinct lets users invoke the slash command explicitly **and** lets the skill auto-trigger on relevant phrases.

The skill bundles the [`prompts/`](../../prompts/) recipes plus the advisory CI YAML in its own directory, so a user project does not depend on the upstream `main` branch at runtime. When you change anything in [`prompts/`](../../prompts/) or [`examples/github-actions/01-advisory-pr-comment.yml`](../../examples/github-actions/01-advisory-pr-comment.yml), sync the bundled copy under `skills/agents-shipgate/`.

## Install in your agent project

From the root of the project where you want `/shipgate` and the skill available:

```bash
# Slash command
mkdir -p .claude/commands
curl -fsSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/.claude/commands/shipgate.md \
  -o .claude/commands/shipgate.md

# Skill (bundled recipes — recursive)
mkdir -p .claude/skills/agents-shipgate
for f in SKILL.md \
         prompts/add-shipgate-to-repo.md \
         prompts/verify-agent-diff.md \
         prompts/fix-top-finding.md \
         prompts/recommend-fixes.md \
         prompts/triage-false-positive.md \
         prompts/stabilize-strict-mode.md \
         prompts/upgrade-shipgate-version.md \
         ci-recipes/advisory-pr-comment.yml; do
  mkdir -p ".claude/skills/agents-shipgate/$(dirname "$f")"
  curl -fsSL "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/skills/agents-shipgate/$f" \
    -o ".claude/skills/agents-shipgate/$f"
done
```

Or, if you have this repo cloned, copy them over:

```bash
cp /path/to/agents-shipgate/.claude/commands/shipgate.md .claude/commands/shipgate.md
cp -r /path/to/agents-shipgate/skills/agents-shipgate .claude/skills/agents-shipgate
```

The `agents-shipgate init --agent-instructions=claude-code-skill` renderer can
also use repo-local overrides without rebuilding the package:

```yaml
schema_version: 1
targets:
  claude-code-skill:
    overrides_dir: .agents-shipgate/adoption-kit/claude-code-skill
```

Files in that directory are relative to `.claude/skills/agents-shipgate/`.
The default config path is `.agents-shipgate/adoption-kit.yaml`; override it
with `--agent-instructions-kit <path>`.

## Verify

Open Claude Code in the project. Two checks:

1. Type `/shipgate` and confirm the command shows up. It should run the bootstrap flow (slash command, NOT the skill).
2. In a fresh chat, ask "add Tool-Use Readiness checks for this agent" without saying the word "shipgate" — the `agents-shipgate` skill should auto-trigger.

If `/shipgate` runs the bootstrap end-to-end, the first path is working. The
first run installs `agents-shipgate` via `pipx`, generates `shipgate.yaml`, and
produces `agents-shipgate-reports/report.json`.

For ongoing PRs, type `/shipgate verify`. Claude Code should read
`prompts/verify-agent-diff.md`, run:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify \
  --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD \
  --ci-mode advisory --format json
```

For local uncommitted work, omit `--base`/`--head` so uncommitted edits are
scanned. For committed PR/CI refs, make the base ref available first because
`verify` never fetches.

It should then summarize `verifier.json.merge_verdict`,
`capability_review.top_changes[]`, `first_next_action.actor`,
`fix_task.safe_to_attempt`, and `report.json.release_decision.decision`.

## Verify an agent PR

The bootstrap flow above wires Shipgate into a repo. The ongoing-PR command is
`verify`. On any PR that changes agent tools, MCP exports, OpenAPI specs,
prompts, permissions, policies, CI gates, or `shipgate.yaml`, Claude Code should
run it before reporting the change as complete:

```bash
agents-shipgate verify --base origin/main --head HEAD --json
```

Then read `agents-shipgate-reports/verifier.json` and **lead with
`merge_verdict`** (`mergeable` / `human_review_required` /
`insufficient_evidence` / `blocked` / `unknown`) — a deterministic projection of
`release_decision.decision`, which stays the gate in
`agents-shipgate-reports/report.json`. Read `capability_review.top_changes[]`
next for the highest-signal tool/action access changes, and check
`trust_root_touched`, `policy_weakened`, and `fix_task`.

Do **not** claim completion when `merge_verdict` is `blocked`,
`insufficient_evidence`, or `human_review_required` unless the user has
explicitly accepted the human-review requirement. Follow `fix_task` as the
repair boundary. When `first_next_action.actor` or `fix_task.actor` is `human`,
surface the item for a person — approval, confirmation, idempotency,
broad-scope, prohibited-action, waiver, baseline, and policy evidence cannot be
synthesized.

Never weaken `shipgate.yaml`, the Shipgate CI workflow, `AGENTS.md`, policy
packs, baselines, waivers, or suppressions merely to make Shipgate pass; that
edit is itself a trust-root change the gate flags. The full walkthrough is in
[`../use-cases/ai-generated-agent-prs.md`](../use-cases/ai-generated-agent-prs.md).

## What the skill knows about

The `agents-shipgate` skill routes to bundled recipes (relative paths inside the skill directory):

- Bootstrap a repo → `prompts/add-shipgate-to-repo.md`
- Verify an agent-related PR or local diff → `prompts/verify-agent-diff.md`
- First-time CI (advisory PR comment) → `ci-recipes/advisory-pr-comment.yml`
- Fix the top finding → `prompts/fix-top-finding.md`
- Recommend fixes across all findings → `prompts/recommend-fixes.md`
- Triage a false positive → `prompts/triage-false-positive.md`
- Promote advisory CI to strict → `prompts/stabilize-strict-mode.md`
- Upgrade the version → `prompts/upgrade-shipgate-version.md`

For the stable CLI / JSON contract the skill relies on, see [`STABILITY.md`](../../STABILITY.md).

## Optional Claude Code hooks

After the verifier CLI and CI are already working, you can install local
Claude Code hooks:

```bash
agents-shipgate install-hooks --target claude-code --write
```

Three hooks are installed:

- **`PreToolUse` (boundary, in-session).** Before `Edit|Write|MultiEdit`
  touches a protected trust-root surface (`shipgate.yaml`, `policies/`,
  the Shipgate CI workflow, agent-instruction files, `.mcp.json`, …),
  the hook routes the call to the human with `permissionDecision:
  "ask"` and an explanation — the same authority semantics as
  `merge_verdict: human_review_required`, surfaced *before* the edit
  happens instead of at PR time. The protected-surface list is rendered
  at install time from the same `TRUST_ROOT_SURFACES` table the
  `SHIP-VERIFY-*` checks classify against, so the in-session boundary
  and the PR gate cannot drift. Set
  `AGENTS_SHIPGATE_PRETOOLUSE_DECISION=deny` for hard blocking, or
  `=allow` to disable the boundary without uninstalling.
- **`PostToolUse` (nudge).** A cheap trigger check after
  `Edit|Write|MultiEdit`, ignoring the manifest-present force-run rule so
  irrelevant docs edits do not nudge every turn.
- **`Stop` (verify).** Full `agents-shipgate verify` only when the
  working tree or current branch has a relevant change that has not
  already been checked.

Local setup failures such as a missing CLI or unavailable base ref are
surfaced as context, not as the release gate. CI remains authoritative,
and changing the hook files or other Shipgate trust roots is itself
visible to verify-mode `SHIP-VERIFY-*` checks.

## Codex / Cursor / Aider

The slash command and skill format are Claude Code-specific. For other coding agents:

- [`use-with-codex.md`](use-with-codex.md) — install the canonical `AGENTS.md` snippet and repo-scoped Codex skill.
- [`use-with-cursor.md`](use-with-cursor.md) — drop the auto-attach `.cursor/rules/agents-shipgate.mdc` rule in for Cursor.
- For Aider (or any other agent without a dedicated guide): paste the body of the relevant `prompts/*.md` file directly. See [`prompts/README.md`](../../prompts/README.md).
