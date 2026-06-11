# Design note: the agent-native governance platform (P3 direction)

Status: **design only** — nothing in this document is shipped, promised, or a
contract. It records the long-term direction so P0–P2 work can avoid painting
us into a corner, and so future implementation PRs have a reviewed starting
point. The current product promise stays narrow: a deterministic, local-first,
static merge gate for AI-generated agent capability changes.

Three pillars, in dependency order:

1. [Org policy pack distribution](#1-org-policy-pack-distribution) — one
   org-level source of truth for release rules, consumed by many repos.
2. [Host-capability governance ("ring 2")](#2-host-capability-governance-ring-2)
   — extend the capability model from *what the built agent can do* to *what
   the coding agent building it is allowed to do*.
3. [Cross-repo attestation registry](#3-cross-repo-attestation-registry) —
   make verifier verdicts durable, queryable evidence across an organization.

Everything here obeys the standing invariants: one decision engine
(`report.json.release_decision.decision` stays the only gate), static-by-default
(no agent execution, no LLM calls, no scanner network calls in the default
path), and human authority is declared, never inferred.

---

## 1. Org policy pack distribution

### Problem

Policy packs (`checks.policy_packs`) are repo-local YAML today. An
organization with 40 agent repos copies the same `org-release.yaml` 40 times;
drift is invisible and unauditable. The org wants: author once, pin
everywhere, bump deliberately.

### Design

**Distribution is just files + pins — no registry service.** An org publishes
a policy-pack repo (or a directory in a monorepo):

```
acme-shipgate-policies/
  packs/
    org-release.yaml
    payments.yaml
  CHANGELOG.md
```

Downstream repos reference it in `shipgate.yaml` with an integrity pin:

```yaml
checks:
  policy_packs:
    - path: vendor/shipgate-policies/org-release.yaml   # vendored copy
      source: github.com/acme/acme-shipgate-policies@v3 # provenance label
      sha256: 4f2a…                                      # integrity pin
```

Key decisions:

- **The scanner never fetches.** Distribution is `git subtree` / `git
  submodule` / a vendoring script / an internal package — out of scope for the
  CLI. The CLI's job is verifying the pin: `sha256` mismatch is a
  `SHIP-VERIFY-*`-class trust-root finding (`SHIP-POLICY-PACK-PIN-MISMATCH`),
  routed to human review, because a silently swapped policy pack is a
  weakened judge.
- **Pack changes are capability changes.** A PR that edits a vendored pack or
  its pin appears in `capability_review` with `policy_weakened` evaluated by
  diffing the pack's effective rules (rule removed / severity lowered /
  scope narrowed ⇒ weakened). This reuses the existing
  `effective_policy` snapshot machinery.
- **Precedence is fixed and boring:** built-in checks < org packs < repo
  manifest overrides, with the existing rule that severity can be raised
  freely but lowering across a tier boundary surfaces in `policy_audit` and
  the packet. No "org pack disables built-in check" mechanism — suppression
  stays repo-local with a required reason, so accountability stays local.
- **Authoring UX, not authoring engine:** natural-language → YAML compilation
  is a coding-agent task (a bundled prompt, like the existing recipes), and
  the YAML is what gets reviewed and pinned. The gate never interprets prose.

### Non-goals

Hosted policy registry, org-wide SaaS dashboards, OPA/Rego embedding, dynamic
policy fetching at scan time.

---

## 2. Host-capability governance ("ring 2")

### Problem

Today Shipgate gates the **built agent's** capability surface (ring 1: MCP
exports, tool definitions, scopes, approval policies). But the riskiest actor
in the loop is often the **coding agent doing the building**: its own
permissions, hooks, MCP servers, and settings are a capability surface too —
`.claude/settings.json` permission allowlists, `.mcp.json` servers granted to
the IDE agent, `.codex/config.toml`, CI workflow permissions. A PR that adds
`"Bash(curl:*)"` to the coding agent's allowlist changes what AI can do in
this repo at least as much as a new `stripe.create_refund` tool.

The v0.12 PreToolUse trust-root guard is the first ring-2 surface: it routes
edits of Shipgate's own trust roots to a human before they happen. This
pillar generalizes that from "protect the gate" to "govern the host."

### Design

- **Extend the capability model, not the engine.** Host capabilities become a
  new `subject_kind` family in the existing `capability_change` /
  `capability_review` blocks: `host_permission` (agent-harness permission
  grants), `host_mcp_server` (MCP servers attached to the coding agent),
  `host_hook` (hooks added/removed/retargeted), `host_ci_permission`
  (workflow `permissions:` and `pull_request_target`-class escalations).
  Adapters parse the static config files only — same trust model as ring 1.
- **Sources** (all static, all already in or near the trigger catalog):
  `.claude/settings.json` + `.claude/hooks/*`, `.mcp.json`, `.cursor/mcp.json`,
  `.codex/config.toml` / `.codex/hooks.json`, `.github/workflows/*` permission
  blocks.
- **Policy vocabulary**: org packs gain `applies_to: {subject_kind:
  host_permission}` rules, e.g. "no network-capable Bash allowlist entries
  without human ack", "MCP servers attached to the IDE agent must be
  enumerated in an inventory file", "workflow permissions may never gain
  `contents: write` in an agent-authored PR".
- **Verdict routing is unchanged**: host-capability findings flow into the
  same release decision; `merge_verdict` stays the single projection. A
  host-permission escalation is `human_review_required` by default — the
  agent that granted itself power cannot self-approve it
  (`stop_reason: self_approval_prohibited` already models this).
- **Runtime enforcement stays out of scope.** Ring 2 is still PR-time review
  of declared configuration. Stopping a live tool call belongs to gateways
  and harness permission systems; we gate what gets merged, not what runs.

### Sequencing

1. Adapter: parse `.claude/settings.json` permissions + hooks into
   `host_permission` / `host_hook` capability facts (read-only, no checks).
2. Diff: surface host-capability deltas in `capability_review.top_changes`.
3. Checks: first three host checks (permission-allowlist expansion, hook
   removal/retarget, MCP server addition without inventory).
4. Org-pack vocabulary for host rules (depends on pillar 1).

---

## 3. Cross-repo attestation registry

### Problem

`agents-shipgate attest` derives a deterministic local attestation from
`verifier.json`, but each repo's attestations live and die in that repo's CI
artifacts. An org cannot answer: "which production agents shipped capability
changes last quarter, who acknowledged the human-review items, and which
merges bypassed a non-mergeable verdict?"

### Design

- **The registry is a dumb, append-only store of signed JSON.** First
  implementation: a git repo (or object-store bucket) of
  `attestations/<repo>/<sha>.json` files pushed by CI after merge. No
  service, no query API in v1 — `jq` over a checkout is the query API.
- **The attestation schema already exists** (`attestation-schema.v0.1.json`).
  Additions needed: repo identity, PR linkage (`base_sha`, `head_sha`,
  `merged_by` as *declared* CI facts, never inferred), and the
  `human_ack` block carried verbatim so acknowledgements are durable.
- **Integrity over authority.** v1 ships content-hash chaining (each
  attestation embeds the previous one's hash per repo) so tampering is
  detectable; real signing (Sigstore/minisign) is a later, optional layer.
  We are building an audit trail, not a PKI.
- **The one query that matters first**: "merges where `merge_verdict` was not
  `mergeable` and no `human_ack` is present." That is the bypass report — the
  org-level version of the benchmark's obedience-under-pressure metric, and
  the commercial wedge for a hosted view later.
- **OSS stance preserved**: local attest + git-push registry remain free OSS.
  If a hosted dashboard emerges it reads the same files and adds nothing the
  CLI cannot produce (per the README pricing stance).

---

## What we deliberately do not build

- A second decision engine anywhere (org server, registry, dashboard).
- Runtime guardrails / tool-call interception.
- LLM-evaluated policies ("the model decides if this is risky").
- Network calls in the default scan path — distribution and registry pushes
  happen in CI steps the user writes, not inside the scanner.

## Revisit triggers

Re-open this note when any of: (a) the W2+ benchmark shows host-capability
edits are a top bypass vector; (b) two or more design partners ask for org
packs in the same quarter; (c) the attestation bypass report is requested by
a security reviewer in a pilot.
