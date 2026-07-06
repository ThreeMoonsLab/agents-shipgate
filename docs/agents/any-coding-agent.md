# Any Coding Agent

Compact control guide for coding agents **without** a named integration —
Cline, Windsurf, Devin, Aider, OpenHands, or anything else that can run a
shell command or speak MCP. The named guides ([`codex.md`](codex.md),
[`claude-code.md`](claude-code.md), [`cursor.md`](cursor.md)) add
harness-specific hooks and skills; everything below works with none of that
installed.

## Force agent mode

Agent mode auto-enables only inside known harnesses (Claude Code exports
`CLAUDECODE=1`, Cursor exports `CURSOR_TRACE_ID`). In any other harness,
export the override so errors carry structured `next_action` /
`next_actions` payloads instead of prose:

```bash
export AGENTS_SHIPGATE_AGENT_MODE=1
```

`AGENTS_SHIPGATE_AGENT_MODE=0` forces it off.

## The control loop

Before reporting an agent-capability change complete, run the local boundary
check and parse the single stdout JSON object
(`shipgate.codex_boundary_result/v1`):

```bash
shipgate check --agent codex --workspace . --format codex-boundary-json
```

`--agent codex` is the generic profile; use it when your harness has no named
profile. Switch on `decision`, `completion_allowed`, `must_stop`,
`first_next_action`, `human_review`, `repair`, and `policy`. Never infer a
control decision from prose, Markdown, or PR comments.

`check` is boundary-only. If your diff adds dynamic, undeclared, or ambiguous
tool capability, do not treat `decision="allow"` as merge readiness — run the
verifier:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json
```

Then read, in order: `agents-shipgate-reports/agent-handoff.json` first
(`gate.merge_verdict`, then `controller`), `verifier.json` as the
authoritative controller substrate, and
`report.json.release_decision.decision` as the release gate. For committed
PR refs add `--base origin/main --head HEAD`; make the base ref available
first because `verify` never fetches.

Before changing host grants (MCP servers, permission rules, hooks), capture
the inventory:

```bash
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass (advisory mode or strict-no-blockers) |
| `2` | Manifest config error |
| `3` | Input parse error |
| `4` | Other Agents Shipgate error |
| `20` | Strict-mode gate failure |

## No shell? Use the MCP server

If your harness prefers MCP tools over shell commands, install the `[mcp]`
extra and register the read-only stdio server (`agents-shipgate mcp-serve`).
It exposes `shipgate.check`, `shipgate.preflight`, `shipgate.explain`,
`shipgate.capabilities`, and `shipgate.handoff` — the same engine, no shell.
See [`../mcp-server.md`](../mcp-server.md).

## Forbidden shortcuts

Regardless of harness: do not suppress findings (`checks.ignore`), lower
severities, expand baselines or waivers, fabricate approval / idempotency /
confirmation evidence, weaken `shipgate.yaml` or agent instructions, or
remove Shipgate CI to make a verdict pass. Trust-root and boundary checks
are suppression-immune; the cheapest reward-hack is also the most visible
one.

## Report friction

If a verdict looked wrong (false positive, missed capability, unclear
`first_next_action`) or an adapter could not see your framework, export a
locally redacted feedback bundle and attach it to an issue:

```bash
agents-shipgate feedback export \
  --from agents-shipgate-reports/verifier.json --redact \
  --out shipgate-feedback.json
```

Issue template:
<https://github.com/ThreeMoonsLab/agents-shipgate/issues/new?template=agent_feedback.yml>
— never attach unredacted reports.
