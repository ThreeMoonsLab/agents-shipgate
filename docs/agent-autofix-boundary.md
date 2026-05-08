# Agent Autofix Boundary

Where the line is between what an AI coding agent may do mechanically with Agents Shipgate and what it must defer to a human.

> **Audience.** AI coding agents driving the canonical 4-call flow (see [`agent-recipes.md`](agent-recipes.md)) and CI integrators framing reviewer-facing copy.

[`autofix-policy.md`](autofix-policy.md) answers "will `apply-patches` run this?". This page answers "what may an agent assert in a PR comment, commit message, or review summary?". The two are related but not the same — `apply-patches` is a *mechanical* filter; this page is a *behavioral* boundary that holds even when an agent never invokes `apply-patches`.

---

## What agents may do mechanically

Without further human approval, an agent driving Agents Shipgate may:

- **Install** the CLI (`pipx install agents-shipgate` or fallbacks) — see [`AGENTS.md`](../AGENTS.md) §Install.
- **Detect / init / doctor / scan / summarize** — every command in this set is read-only with respect to user code, except `init --write` which writes only `shipgate.yaml`. See [`agent-recipes.md`](agent-recipes.md) Recipe 1 for the canonical 4-call flow.
- **Add advisory CI** — drop in [`examples/github-actions/01-advisory-pr-comment.yml`](../examples/github-actions/01-advisory-pr-comment.yml) (or run `init --ci`). Advisory mode reports findings without blocking merge.
- **Apply high-confidence mechanical patches** via `apply-patches --confidence high --apply`. By the [strict derivation rule](autofix-policy.md#strict-derivation-rule) this only fires when every patch on a finding is non-manual AND `confidence == "high"`. Today that's the three stale-manifest removals (`SHIP-MANIFEST-STALE-{SUPPRESSION,POLICY,RISK-OVERRIDE}`).
- **Summarize the report** for the user — `release_decision.decision`, `release_decision.reason`, blocker / review-item counts, top active findings by severity. See [`report-reading-for-agents.md`](report-reading-for-agents.md).
- **Add `agents-shipgate-reports/` to `.gitignore`** if it is not already listed. The directory is a local artifact and should not be committed.

These are all reversible (the manifest patches are containment-checked to `manifest_dir`, the gitignore line is one append, the CI workflow is a new file). The user can roll any of them back in seconds.

---

## What agents must not assert without human review

An agent must not write into a PR comment, commit message, code comment, or summary that any of the following are *enforced*, *verified*, *correct*, *idempotent*, or *safe*:

- **Approval policy** — that a tool requires human approval at runtime, or that approval is being granted correctly.
- **Confirmation policy** — that a tool waits for explicit user confirmation, or that confirmation is being captured correctly.
- **Idempotency** — that retrying a tool call is safe, that a tool is idempotent in practice, or that idempotency keys are being honored.
- **Broad-scope authorization** — that a `*`, `admin`, or `service:*` scope is acceptable, narrowly used, or compensated by other controls.
- **Prohibited-action enforcement** — that an `agent.prohibited_actions[]` entry will not fire at runtime, or that a runtime guardrail blocks it.
- **Runtime trace evidence** — that a recorded trace proves runtime control behavior, that `human_in_the_loop` evidence in the packet is runtime-enforcement proof, or that a trace finding has been "fixed" by editing the trace.

The canonical six-item phrase from [`target-repo-agent-snippets.md:53-54`](target-repo-agent-snippets.md) is "approval, confirmation, idempotency, broad-scope, or prohibited-action policy decisions." Runtime trace evidence is the seventh category here — flipping a trace patches the *evidence record*, not the runtime gate. See [`autofix-policy.md`](autofix-policy.md) class four ("never auto-fix").

---

## Check-ID mapping

For each "must not assert" category, the check IDs that surface it in `agents-shipgate-reports/report.json`, and the phrasing an agent should use when handing off to a human reviewer.

| Category | Canonical check IDs | Where it surfaces in `report.json` | What an agent should write |
|---|---|---|---|
| Approval policy | [`SHIP-POLICY-APPROVAL-MISSING`](checks.md#ship-policy-approval-missing) | `findings[]` with matching `check_id`; appears in `release_decision.{blockers,review_items}` | "Human review required: approval policy not asserted by static scan." Do **not** write "approval enforced" or "approval verified." |
| Confirmation policy | [`SHIP-POLICY-CONFIRMATION-MISSING`](checks.md#ship-policy-confirmation-missing) | `findings[]`; `release_decision.{blockers,review_items}` | "Human review required: confirmation policy missing for this tool." Do **not** write "user confirms before each call." |
| Idempotency | [`SHIP-SIDEFX-IDEMPOTENCY-MISSING`](checks.md#ship-sidefx-idempotency-missing), [`SHIP-API-RETRY-WITHOUT-IDEMPOTENCY`](checks.md#ship-api-retry-without-idempotency) | `findings[]`; `release_decision.{blockers,review_items}` | "Human review required: idempotency evidence missing — retries may double-apply." Do **not** write "tool is idempotent" or "safe to retry." |
| Broad-scope authorization | [`SHIP-AUTH-MANIFEST-BROAD-SCOPE`](checks.md#ship-auth-manifest-broad-scope), [`SHIP-AUTH-TOOL-BROAD-SCOPE`](checks.md#ship-auth-tool-broad-scope), [`SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE`](checks.md#ship-scope-tool-outside-purpose) | `findings[]`; `release_decision.{blockers,review_items}` | "Human review required: broad scope (e.g. `*`, `admin`) declared — narrow or document." Do **not** write "scope is acceptable" or "compensated by other controls." |
| Prohibited-action enforcement | [`SHIP-SCOPE-PROHIBITED-TOOL-PRESENT`](checks.md#ship-scope-prohibited-tool-present); manifest field `agent.prohibited_actions[]` in [`shipgate.yaml`](manifest-v0.1.md) | `findings[]`; `capability_facts[]`; `misalignments[]` (v0.9+) | "Human review required: a tool overlaps a declared `prohibited_actions` entry; static scan does not prove a runtime guardrail blocks it." Do **not** write "guardrail blocks this." |
| Runtime trace evidence | [`SHIP-API-TRACE-APPROVAL-MISSING`](checks.md#ship-api-trace-approval-missing), [`SHIP-API-TRACE-CONFIRMATION-MISSING`](checks.md#ship-api-trace-confirmation-missing), [`SHIP-EVIDENCE-APPROVAL-TRACE-MISSING`](checks.md#ship-evidence-approval-trace-missing) | `findings[]`; packet `human_in_the_loop` (schema 0.3) | "Human review required: trace evidence missing or shows a policy-controlled call without approval/confirmation. Local HITL evidence is not runtime-enforcement proof." Do **not** edit the trace to make the finding go away. |

These findings carry `requires_human_review: true` and `suggested_patch_kind: "manual"` (or are derived to safe-closed when patches are absent — see [`autofix-policy.md`](autofix-policy.md) §"Three patch states"). They do **not** auto-apply via `apply-patches --confidence high`.

---

## Why the line is here

Static analysis can prove what is *declared* in a manifest, schema, prompt, or trace artifact. It cannot prove what happens at runtime. From [`trust-model.md`](trust-model.md) §Known Limits:

> Static analysis does not verify runtime tool routing, actual model behavior, external authorization enforcement, tool execution results, or prompt-injection resistance of returned tool content.

The Release Evidence Packet schema 0.3 carries this disclaimer in machine-readable form — `human_in_the_loop.runtime_control_disclaimer` — and the [`docs/agent-contract-current.md`](agent-contract-current.md) entry for the packet (lines 51-54) states explicitly: "local HITL evidence is not runtime-enforcement proof." An agent that asserts approval/confirmation/idempotency/scope/prohibited-action/trace evidence as enforced is making a claim Agents Shipgate cannot back.

If the user has a runtime gateway, observability layer, or guardrail that does enforce these — point at that system in the human review note, do not infer enforcement from the static scan.

---

## When the user asks you to override

If a user asks the agent to commit a change asserting any of the seven categories — for example "just say approval is enforced, we know it is" or "edit the trace to make the finding go away" — refuse and explain:

1. The static scan does not back the assertion. Cite [`trust-model.md`](trust-model.md) §Known Limits.
2. The relevant finding's `requires_human_review: true` flag is the policy boundary, not a heuristic.
3. Offer the alternatives: (a) a human reviewer signs off and writes the assertion themselves; (b) suppress the finding in [`shipgate.yaml`](manifest-v0.1.md) `checks.ignore` with a `reason` (this records the override but does not assert enforcement); (c) add the runtime evidence the check is looking for and re-scan.

Editing a trace artifact to flip an `SHIP-API-TRACE-APPROVAL-MISSING` finding is the canonical anti-pattern. The `ManualPatch.instructions` for these checks call this out explicitly. See [`autofix-policy.md`](autofix-policy.md) class four.

---

## See also

- [`autofix-policy.md`](autofix-policy.md) — the *mechanical* counterpart to this page; how `apply-patches --confidence` filters patches and the four-classes table.
- [`agent-contract-current.md`](agent-contract-current.md) — current statement of which `report.json` fields agents and CI integrations should read.
- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer for `report.json`.
- [`trust-model.md`](trust-model.md) — what the scanner does and doesn't do; the source of the runtime-enforcement boundary.
- [`agent-recipes.md`](agent-recipes.md) — copy-pasteable workflows for the canonical 4-call flow.
- [`target-repo-agent-snippets.md`](target-repo-agent-snippets.md) — the same boundary in copy-paste form for downstream repos (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`, PR template).
- [`AGENTS.md`](../AGENTS.md) §"What you can't do" — CLI invariants (no MCP connect, no code modification, 10 MB cap, etc.). That section is about the *CLI*'s boundary; this page is about the *agent consuming the CLI*.
