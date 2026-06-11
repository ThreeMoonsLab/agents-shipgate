# Use Agents Shipgate with Claude Code

Three pieces of agent-facing surface ship with this repo. They differ in how
reliably they fire, and you should install them in that order:

| Surface | Firing mechanism | Source |
|---|---|---|
| Claude Code hooks (**recommended**) | Deterministic — the Claude Code harness executes them; they do not depend on the model remembering an instruction, so they keep working on long sessions | `agents-shipgate install-hooks --target claude-code --write` |
| `agents-shipgate` skill | Probabilistic — auto-discovered when the conversation touches MCP/tool/permission/policy changes or mentions Shipgate artifacts. Routes to bundled recipes. | [`skills/agents-shipgate/SKILL.md`](../../skills/agents-shipgate/SKILL.md) |
| `/shipgate` slash command | Human-initiated — bootstrap flow: install → `init --write` → fill placeholders → `scan` → report top findings | [`.claude/commands/shipgate.md`](../../.claude/commands/shipgate.md) |

## Recommended setup (two commands)

From the root of your agent project:

```bash
pipx install agents-shipgate
agents-shipgate init --workspace . --write --claude-code
```

This writes the `CLAUDE.md` managed block, the auto-discoverable
`.claude/skills/agents-shipgate/` skill, an `agents-shipgate verify --json`
alias in Makefile / package.json scripts when those files exist, and the
Claude Code hooks described in
[Hooks: the deterministic path](#hooks-the-deterministic-path-recommended).
Inside Claude Code, agent mode auto-enables (the harness exports
`CLAUDECODE=1`), so a zero-flag `agents-shipgate verify` prints the compact
agent result on stdout — no `AGENTS_SHIPGATE_AGENT_MODE=1` prefix needed.

## Hooks: the deterministic path (recommended)

```bash
agents-shipgate install-hooks --target claude-code --write
```

Instruction files and skills are probabilistic surfaces: they depend on the
model noticing and remembering them, and compliance degrades as a session
grows. The hooks are the one surface the Claude Code harness executes
deterministically:

- **PreToolUse** (`Edit|Write|MultiEdit`): a trust-root guard that fires
  BEFORE the edit happens. When the target path is a Shipgate trust root —
  `shipgate.yaml`, `.agents-shipgate/` baselines and waivers, `policies/`,
  the Shipgate CI workflow, the hook files themselves, or an
  `AGENTS.md`/`CLAUDE.md` that carries the managed Shipgate block — the hook
  returns `permissionDecision: "ask"` with the reason, routing the edit to a
  human permission prompt. It never denies outright, so ordinary work is
  never blocked by a hook failure; it converts "the agent quietly weakened
  the gate" into "the user was asked first."
- **PostToolUse** (`Edit|Write|MultiEdit`): a cheap trigger check that adds
  context only when the edited path matches the trigger catalog — ignoring
  the manifest-present force-run rule so irrelevant docs edits do not nudge
  every turn.
- **Stop**: a full `agents-shipgate verify` that runs only when the working
  tree or current branch has a relevant change that has not already been
  checked, before Claude Code reports the work complete.

Local setup failures such as a missing CLI or unavailable base ref are
surfaced as context, not as the release gate. CI remains authoritative, and
changing the hook files or other Shipgate trust roots is itself visible to
verify-mode `SHIP-VERIFY-*` checks.

The skill is named `agents-shipgate`, not `shipgate`, on purpose: Claude Code lets a skill with the same name as a command preempt it, which would silently bypass the `/shipgate` slash command. Keeping the names distinct lets users invoke the slash command explicitly **and** lets the skill auto-trigger on relevant phrases.

The skill bundles the [`prompts/`](../../prompts/) recipes plus the advisory CI YAML in its own directory, so a user project does not depend on the upstream `main` branch at runtime. When you change anything in [`prompts/`](../../prompts/) or [`examples/github-actions/01-advisory-pr-comment.yml`](../../examples/github-actions/01-advisory-pr-comment.yml), sync the bundled copy under `skills/agents-shipgate/`.

## Manual install (without the CLI renderer)

Prefer the [recommended setup](#recommended-setup-three-commands) above. If
you cannot run `init`, fetch the surfaces directly. From the root of the
project where you want `/shipgate` and the skill available:

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
`verify` never fetches. The `AGENTS_SHIPGATE_AGENT_MODE=1` prefix is optional
inside Claude Code — agent mode auto-detects via the harness's `CLAUDECODE=1`
env var, and `--json` prints the compact agent result on stdout.

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

## Codex / Cursor / Aider

The slash command and skill format are Claude Code-specific. For other coding agents:

- [`use-with-codex.md`](use-with-codex.md) — install the canonical `AGENTS.md` snippet and repo-scoped Codex skill.
- [`use-with-cursor.md`](use-with-cursor.md) — drop the auto-attach `.cursor/rules/agents-shipgate.mdc` rule in for Cursor.
- For Aider (or any other agent without a dedicated guide): paste the body of the relevant `prompts/*.md` file directly. See [`prompts/README.md`](../../prompts/README.md).
