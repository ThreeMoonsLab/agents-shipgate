# Agents Shipgate Report

Project: google-adk-cold-start-agent
Agent: adk-ops-agent
Target: production\_like

See `packet.md` for the reviewer-shaped Release Evidence Packet.

## Capability Surface

Surface: 9 tools from 3 sources.
Effects: 1 read, 7 write, 1 financial write.
Write/destructive actions: issue\_goodwill\_refund \(financial write\), assemble\_case\_timeline \(write\), list\_case\_attachments \(write\), ops.append\_case\_note \(write\), ops.export\_case\_bundle \(write\), ops.queue\_backfill \(write\), record\_case\_outcome \(write\), update\_case\_index \(write\).

## Top Findings

1 finding across 1 subject, most urgent first.

- adk-ops-agent \(agent-wide\) \(at shipgate.yaml\) — review \(1 medium\)
  - medium SHIP-ADK-EVAL-COVERAGE-MISSING — Google ADK eval coverage is not declared
    - Declare ADK eval files that cover expected responses and tool-use trajectories for this release.

## Release Decision

Decision: insufficient_evidence
Reason: Insufficient evidence: a tool source has no declared authority \(adk\_ops \[tool\_source\]\). Fix at shipgate.yaml\#tool\_sources\[id='adk\_ops'\].authority. Context: 10 semantic evidence gap\(s\); scan results are not trustworthy enough to gate release.

Blockers (0): none

Review items (1):
- MEDIUM SHIP-ADK-EVAL-COVERAGE-MISSING — Google ADK eval coverage is not declared

Evidence coverage: static (9/9 catalog tools reachable; 10 semantic evidence gap(s); 2/9 actions pass-eligible)

Coverage boundary: what a scan can establish per input and declaration shape — https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/determinism-boundary.md

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 0
- High: 0
- Medium: 1
- Low: 0
- Suppressed: 0
- Status: Warnings detected (legacy; see Release Decision above)

## Finding Provenance

Reviewer triage signal only. Provenance kind does not change severity, release decision, fingerprints, baselines, or CI exit codes.

| Provenance kind | Active findings |
| --- | ---: |
| `static_declaration` | 1 |
| `ast_extraction` | 0 |
| `keyword_heuristic` | 0 |
| `regex_heuristic` | 0 |
| `policy_pack` | 0 |
| `runtime_trace` | 0 |

Suppressed findings excluded: 0

## Capability <-> Intent Diff

Agent intent:

- declared\_purpose: review support cases and route refunds (tags: financial\_action)
- instruction\_preview: Review support cases and route refunds for approval. (tags: financial\_action)

Actual capabilities:

- issue\_goodwill\_refund: capability=financial\_action, risk=financial\_action, control=present

Policy/control gaps:

- MEDIUM control\_missing: Google ADK eval coverage is not declared.
  Requires: Production-like framework releases should declare eval coverage.
  Release implication: Release lacks validation evidence for production-like ADK behavior.

Release implication:

- Static evidence is incomplete; capability/intent analysis may miss release-relevant signal — gather deeper sources before shipping.

Next validation:

- High-risk tool validation case: A declared test or review scenario covers the high-risk tool path.

## Recommended Next Actions

- Declare ADK eval files that cover expected responses and tool-use trajectories for this release.

## Tool Surface Summary

- Total tools: 9
- High-risk tools: 1
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: google_adk_function=5, mcp=3, openapi=1

## Action Surface Diff

- Status: disabled - No action-surface comparison source was provided.
- Base: none

## Capability Runtime Evidence

No local runtime trace artifacts were declared for capability evidence.

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## Google ADK Surface Summary

- Python entrypoints: 1
- Agent config files: 0
- Agents: 1
- Function tools: 5
- Long-running tools: 0
- Toolsets: 2
- Dynamic or unresolved toolsets: 0
- Callbacks: 0
- Plugins: 0
- Eval files: 0

## Findings By Category

### Adk

- MEDIUM: SHIP-ADK-EVAL-COVERAGE-MISSING - Google ADK eval coverage is not declared

## Agent Binding Surface

Status: structural
Root agent: adk\_ops\_agent \[adk\_ops\]
Entry points: adk\_ops\_agent \[adk\_ops\]
Pass eligible: true
Catalog partition: 9 reachable, 0 possible, 0 unbound

## Appendix: Root-Reachable Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| assemble\_case\_timeline | google\_adk\_function | \- | \- | \- | \- |
| support.get\_update\_history | mcp | read\_only | read\_only=high | \- | \- |
| record\_case\_outcome | google\_adk\_function | \- | \- | \- | \- |
| ops.append\_case\_note | openapi | write | write=high | \- | \- |
| issue\_goodwill\_refund | google\_adk\_function | financial\_action | financial\_action=high | \- | \- |
| list\_case\_attachments | google\_adk\_function | \- | \- | \- | \- |
| ops.queue\_backfill | mcp | \- | \- | \- | \- |
| update\_case\_index | google\_adk\_function | \- | \- | \- | \- |
| ops.export\_case\_bundle | mcp | \- | \- | ops:cases:read | \- |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
