# Agent Entry Points

This directory is for coding agents and agent-tooling integrations that need to
discover, run, and report on Agents Shipgate without reading the whole repo.

## Start Here

Use the machine-readable discovery file first:

```text
https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/.well-known/agents-shipgate.json
```

It points to the current schemas, trigger catalog, install commands, preferred
agent read order, MCP tools, and feedback loop. For a single text reference,
fetch [`../../llms.txt`](../../llms.txt); for the longer one-fetch reference,
fetch [`../../llms-full.txt`](../../llms-full.txt).

## Install into your coding agent

**Claude Code** — two commands wire the full surface:

```bash
pipx install agents-shipgate
agents-shipgate init --workspace . --write --claude-code
```

`init --claude-code` writes the `CLAUDE.md` managed block, the
auto-discoverable `.claude/skills/agents-shipgate/` skill, and the Claude Code
hooks: a cheap trigger check after `Edit|Write|MultiEdit` and the full verifier
at `Stop`, so capability changes are re-checked before the agent reports work
complete — even on long sessions where instruction files lose attention. CI
stays authoritative; the hooks are the local feedback loop. Inside Claude Code,
agent mode auto-enables, so a zero-flag `agents-shipgate verify` prints the
compact agent result. Slash command, skill internals, and manual paths:
[`use-with-claude-code.md`](use-with-claude-code.md).

Prefer a plugin over a committed kit? This repository is also a Claude Code
plugin marketplace — the skill-only symmetric counterpart of the Codex plugin
below (workflows, not the scanner binary; install the CLI separately):

```bash
/plugin marketplace add ThreeMoonsLab/agents-shipgate
/plugin install agents-shipgate@agents-shipgate
```

The plugin ships the auto-triggering `agents-shipgate` skill and the
`/agents-shipgate:shipgate` command (plugin commands are namespaced). It does
not ship hooks — install those explicitly with `agents-shipgate install-hooks
--target claude-code --write`, which requires the CLI on `PATH`.

**Codex** — install the skill-only plugin from this repository's marketplace,
or write the repo-scoped kit directly:

```bash
codex plugin marketplace add ThreeMoonsLab/agents-shipgate   # plugin path
agents-shipgate init --workspace . --write --agent-instructions=agents-md,codex-skill  # committed path
```

Then invoke `$agents-shipgate` in a fresh thread. The plugin supplies
workflows, not the scanner binary — install the CLI (`pipx install
agents-shipgate && pipx upgrade agents-shipgate`) where Codex runs commands and
require contract v15 or newer. Marketplace details, kit overrides, and the
beta-migration steps: [`use-with-codex.md`](use-with-codex.md).

**Cursor** — `init --agent-instructions=cursor` writes the auto-attach rule;
see [`use-with-cursor.md`](use-with-cursor.md).

The prompt to paste into any other coding agent is in
[`../target-repo-agent-snippets.md`](../target-repo-agent-snippets.md#paste-into-a-coding-agent).

## Local Agent Control

Run one local boundary check before reporting an agent-capability change
complete:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
```

Use `--agent claude-code` for Claude Code and `--agent cursor` for Cursor.
Parse stdout as `shipgate.agent_boundary_result/v2`; switch on
`control.state`, follow `control.next_action`, `control.allowed_next_commands`,
and `control.human_review`, and treat `decision` as diagnostic context only.
Do not infer control from prose.

For committed PR verification, run `agents-shipgate verify`, then read
`agents-shipgate-reports/current-control.json` first — it names which run is
current and refuses the read when HEAD or the working tree has moved since the
decision. Then validate the receipt it binds, read `agent-handoff.json`
(`control.state`, then `gate.merge_verdict`), and use
`report.json.release_decision.decision` as the release gate.

The normative local protocol is [`protocol.md`](protocol.md). Per-agent compact
control guides are [`codex.md`](codex.md), [`claude-code.md`](claude-code.md),
and [`cursor.md`](cursor.md). Any other agent — Cline, Windsurf, Devin, Aider,
OpenHands, or anything with a shell or MCP client — uses
[`any-coding-agent.md`](any-coding-agent.md) (force agent mode with
`AGENTS_SHIPGATE_AGENT_MODE=1`, then the same control loop). Full installation
and workflow guides live in [`use-with-codex.md`](use-with-codex.md),
[`use-with-claude-code.md`](use-with-claude-code.md), and
[`use-with-cursor.md`](use-with-cursor.md).

## Proactive Feedback

If a Shipgate run produces a false positive, misses a capability, allows an
unsafe-looking change, or gives a confusing next action, export redacted
feedback before filing an issue:

```bash
agents-shipgate feedback export --from agents-shipgate-reports/verifier.json \
  --redact --out shipgate-feedback.json
```

Then open an agent feedback issue:

```text
https://github.com/ThreeMoonsLab/agents-shipgate/issues/new?template=agent_feedback.yml
```

Attach `shipgate-feedback.json` and the smallest safe manifest/tool-source
snippet that reproduces the behavior. Do not attach unredacted reports, raw
tool outputs, secrets, customer data, or chain-of-thought.

When the feedback exposes a missed governance behavior, add or request the
smallest reproducible case in [`../../benchmark/agent-pr-governance/`](../../benchmark/agent-pr-governance/)
so future coding agents can be tested against it deterministically.
