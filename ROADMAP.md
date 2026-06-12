# Roadmap

> **Naming.** This project is **Agents Shipgate** (display name) / `agents-shipgate` (package, CLI, repo). See [`AGENTS.md` § Naming (canonical)](AGENTS.md#naming-canonical) for the full convention.

**Latest release: `v0.13.0`** — the **agent-native protocol** cycle.

## What Agents Shipgate is

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes. When a coding agent (Codex, Claude Code, Cursor — or a
human) opens a PR that changes what an AI agent can *do*, Shipgate compiles the
diff into a capability delta, evaluates it against a protected release policy,
and returns a machine-readable verdict — `merge_verdict`,
`can_merge_without_human`, `first_next_action`, `fix_task` — so the agent knows
whether to **continue, repair, or stop for human authority**.

The release gate is one decision engine: `report.json.release_decision.decision`.
Every agent-facing field in `verifier.json` is a deterministic projection of it;
nothing gates independently.

## Lead wedge (focus)

Two real surfaces share one engine: **(A)** tool-surface readiness for teams that
declare an agent's capabilities in-repo, and **(B)** boundary governance over what
a *coding agent* changes in any repo — host config, MCP permission grants, CI
gates, and agent instructions. **B is the lead wedge.** Its inputs are the
declarative host files Shipgate already reads with high fidelity, it delivers a
first verdict with no manifest, and its audience is every team running Claude
Code, Codex, or Cursor — not only teams building tool-using agents. A stays the
depth story: the capability-delta engine that makes the verdict precise.

What this locks: lead positioning, the zero-config first-run experience, and the
proof effort all target B. The next unit of work is **proof and activation on
that wedge** — a labeled verdict-accuracy benchmark and default-on, hard-to-skip
checks — **not** more host surface. New surface of any kind is governed by the
[non-goals](#explicit-non-goals) below and the surface-discipline gate in
[`CONTRIBUTING.md`](CONTRIBUTING.md#surface-discipline).

## Direction

The engine pivoted to the verifier in `v0.12.0`. The next leg is to make the
**agent-native authority protocol explicit** and to turn real coding-agent runs
into replayable evidence. Active themes, in priority order:

1. **Agent-Native Merge Contract, documented.** One page that maps the eight
   contracts — trigger, capability change, merge verdict, repair, forbidden
   action, human authority, trust root, attestation — to the artifacts that
   already implement them (`docs/triggers.json`, `capability_change`,
   `merge_verdict`, `fix_task`, the `SHIP-VERIFY-*` trust-root checks,
   `human_ack`, `verifier.json`). Document the protocol substrate that already
   exists; do not invent new architecture.

2. **Workflow-evidence flywheel.** *(First cut shipped — `agents-shipgate
   feedback capture`.)* An opt-in, locally redacted *Agent Workflow Evidence*
   capture from a verify before/after pair: the verdict transition, a
   gate-integrity signal (`suspected_gate_bypass`), the capability delta, and
   prompt / diff / transcript provenance. Every real pilot PR becomes a
   replayable benchmark scenario, so the deterministic verdict is
   regression-tested against real agent behavior — not just synthetic
   archetypes. The local event and replay-bundle contracts are documented in
   [`docs/agent-workflow-evidence.md`](docs/agent-workflow-evidence.md), and the
   initial governance case catalog lives under
   [`benchmark/agent-pr-governance/`](benchmark/agent-pr-governance/).
   Remaining: full raw-bundle replay and a `scenario replay` harness.

3. **Pre-emptive authority surface.** Today the trust root is enforced
   *reactively* — a weakening shows up in the diff, and Shipgate escalates.
   Surface the boundary *before* the agent acts: a standing forbidden-action /
   protected-surface projection in `verifier.json`, and a `verify --preview`
   pre-flight, so an agent learns what it must not touch without first tripping
   the gate.

4. **Local attestation.** *(First cut shipped — `agents-shipgate attest`.)* A
   deterministic, JSON-first, local attestation per verdict — base/head SHA,
   changed capability IDs, policy-snapshot hash, CLI version, verdict,
   `human_ack` state, and artifact hashes — as the durable record of *which*
   capability was released, under *which* verdict, acknowledged by *whom*.
   Remaining: explicit waiver/baseline state and a cross-repo capability
   registry that consumes these attestations.

5. **Source-provenance enrichment (incremental).** Thread origin (file path,
   line index for JSONL, list index for arrays) through finding evidence to
   expand the mechanical-patch catalog — never approval, confirmation, or
   idempotency evidence, which stay manual permanently.

6. **Host-capability governance (first cut shipped).** Gating what the coding
   agent itself is allowed to do in a repo ("ring 2"): `audit --host` with
   host-grant drift detection, plus MCP-permission and host-boundary checks, are
   live. This is the **lead wedge** (see [Lead wedge (focus)](#lead-wedge-focus));
   the near-term work on it is proof and activation, not more host surface. Org
   policy-pack distribution with integrity pins and a cross-repo attestation
   registry remain **design only** —
   [docs/engineering/agent-native-governance-platform.md](docs/engineering/agent-native-governance-platform.md)
   sketches them so nearer-term work does not foreclose it; nothing there is
   shipped or promised.

### Explicit non-goals

- **More framework adapters is not the roadmap.** The moat is the deterministic
  trust root and reward-hacking resistance, not breadth of input parsers. New
  adapters (AutoGen, Semantic Kernel, LlamaIndex, additional language surfaces)
  are accepted only when a real workflow needs one — they do not advance the
  core thesis and are not a priority.
- **No second verdict.** Nothing gates independently of
  `release_decision.decision`. Every new surface is a projection of it.
- **No agent execution, LLM calls, MCP connections, network access, or scanner
  telemetry** in the default static path. Runtime inventory stays an explicit,
  trust-gated, opt-in command — never part of default CI.

## Release history

Releases `v0.2` through `v0.13.0` are complete. Highlights:

- **`v0.13.0` — Agent-native protocol.** `shipgate check`; the shared
  `agent_result_v1` contract for Codex, Claude Code, and Cursor; deterministic
  policy discovery; repair-loop routing; and the read-only `shipgate.check`
  MCP adapter.
- **`v0.12.0` — Verifier cycle.** `agents-shipgate verify`; `verifier.json`
  (`merge_verdict`, `can_merge_without_human`, `first_next_action`, `fix_task`,
  `capability_review`); `pr-comment.md`; diff-aware trust-root checks
  (`SHIP-VERIFY-*`: policy-weakened, baseline/waiver-expanded, CI-gate-removed,
  agent-instructions-weakened, capability-scope-broadened); `human_ack`;
  `capability_change`; and the agent-adoption harness with
  adversarial-obedience and verify-restraint scoring.
- **`v0.8.0` — Release decision engine.** `release_decision.decision` as the
  single, baseline-aware gating signal across CLI, JSON, Markdown, PR comments,
  and Action outputs.
- **`v0.6.0`–`v0.7.0` — Agent-friendly adoption.** `detect`, auto-detecting
  `init`, `--suggest-patches` / `apply-patches`, and per-check autofix metadata
  (`autofix_safe`, `requires_human_review`).
- **`v0.3.0`–`v0.5.0` — Static framework coverage.** Google ADK,
  LangChain/LangGraph, CrewAI, SARIF output, external policy packs, and baseline
  diff mode — static-by-default throughout.
- **`v0.2` — Onboarding and CI.** `init`, `doctor`, `self-check`, fixtures,
  baseline save/apply, SBOM generation, and release signing.

## Static-by-default principles

All adapters are read-only: local file parsing only; no agent run, model call,
tool call, MCP connection, or network access. Callbacks, plugins, and guardrail
declarations are static evidence, not proof of runtime enforcement. Dynamic
toolsets must produce warnings or `insufficient_evidence` findings unless the
user supplies explicit MCP, OpenAPI, or tool-inventory inputs.
