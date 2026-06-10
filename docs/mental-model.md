# The 5-Minute Mental Model

Agents Shipgate looks big — many artifacts, many schemas. The model
underneath is one sentence:

> **One engine produces one verdict; everything else is a projection of
> that verdict for a specific reader.**

## The one engine

`report.json.release_decision.decision` is the only gate:

```
blocked | review_required | insufficient_evidence | passed
```

It is computed deterministically from findings (each explained by a
`contribution_rules[]` audit row), evidence coverage, and the baseline.
Nothing else gates. Every other surface — `merge_verdict`, PR comments,
Action outputs, Check Runs, the MCP server — restates this decision for a
different audience and can never disagree with it.

## The projections, by reader

| Artifact | Reader | When to read | Can you skip it? |
|---|---|---|---|
| `verifier.json` | **coding agent** | first, on every PR verify | No — it leads with `merge_verdict`, `can_merge_without_human`, `first_next_action`, `fix_task` |
| `pr-comment.md` | **human reviewer** | in the PR thread | Yes if you read the Check Run / report |
| `report.json` | **tools, CI, auditors** | when gating or debugging a verdict | No for CI gating (`release_decision.decision` is the source of truth) |
| `report.md` | **human release reviewer** | release review | Yes — same content as report.json, prose-shaped |
| `report.sarif` | **GitHub code scanning** | Security tab / annotations | Yes unless you use code scanning |
| `packet.{md,json,html}` | **GRC / security reviewer** | formal release evidence | Yes for day-to-day PRs |
| `agent-result.json` | **PR controllers / bots** | compact allow/warn/review/block routing | Yes unless you build automation |
| `suggested-inventory.json` | **whoever fixes `insufficient_evidence`** | when evidence gaps exist | Yes when confidence is high |
| capability lock / diff | **external integrations, research** | cross-repo capability tracking | Yes — never gates |
| attestation | **release record keepers** | after merge | Yes — durable record, not a gate |

If you remember nothing else: **agents read `verifier.json` first; CI
gates on `report.json.release_decision.decision`; humans read the PR
comment.** Everything else is optional depth.

## The protected spine (why agents can't cheat it)

The verdict would be worthless if the agent being gated could edit the
gate. Three mechanisms prevent that:

1. **Unsuppressible categories.** `SHIP-VERIFY-*`,
   `SHIP-CODEX-BOUNDARY-*`, and `SHIP-HOST-BOUNDARY-*` findings ignore
   `checks.ignore` — the manifest cannot silence them.
2. **Severity floors.** Downgrades past a check's floor are config
   errors; tier-crossing downgrades need explicit, expiring
   acknowledgements.
3. **Trust-root surveillance.** Any diff touching the manifest,
   policies, baselines, Shipgate CI, agent instructions, or host config
   (`.mcp.json`, `.claude/settings.json`, …) routes to human review —
   and `policy_weakened` / `trust_root_touched` force
   `can_merge_without_human: false` even on otherwise-mergeable PRs.

Run `agents-shipgate fixture run agent_weakens_gate` to watch all three
fire at once.

## The five inputs, one sentence each

- **Manifest (`shipgate.yaml`)** — what the agent is *supposed* to do and
  which policies cover it.
- **Tool sources** — what the agent *can* do (MCP exports, OpenAPI specs,
  framework code, inventories).
- **Policy packs** — your organization's extra release rules, as data.
- **The diff (base→head)** — what this PR *changes*, compiled into a
  capability delta.
- **Host config** — what your *coding agent* is allowed to do
  (`SHIP-HOST-BOUNDARY-*`, `audit --host`).

## What Shipgate is not

Static, local, deterministic — therefore it does not run agents, call
LLMs, connect to MCP servers, prove runtime enforcement, or replace
dynamic security testing. The verdict is about *declared* capability and
*declared* policy, reviewed before promotion. See
[`trust-model.md`](trust-model.md) for the precise boundary.
