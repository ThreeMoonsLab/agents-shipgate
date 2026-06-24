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

## Local Agent Control

Run one local boundary check before reporting an agent-capability change
complete:

```bash
shipgate check --agent codex --workspace . --format codex-boundary-json
```

Use `--agent claude-code` for Claude Code and `--agent cursor` for Cursor.
Parse stdout as `shipgate.codex_boundary_result/v1`; switch on `decision`,
`completion_allowed`, `must_stop`, `first_next_action`, `human_review`,
`repair`, and `policy`. Do not infer a control decision from prose.

For committed PR verification, run `agents-shipgate verify`, then read
`agents-shipgate-reports/agent-handoff.json` first and
`report.json.release_decision.decision` as the release gate.

The normative local protocol is [`protocol.md`](protocol.md). Per-agent compact
control guides are [`codex.md`](codex.md), [`claude-code.md`](claude-code.md),
and [`cursor.md`](cursor.md). Full installation and workflow guides live in
[`use-with-codex.md`](use-with-codex.md),
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
