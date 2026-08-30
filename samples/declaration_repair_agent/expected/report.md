# Agents Shipgate Report

Project: declaration-repair-agent
Agent: support-billing-agent
Target: production\_like

## Release Decision

Decision: insufficient_evidence
Reason: Insufficient evidence: a declared effect does not account for the evidence inferred for it \(billing.cancel\_invoice\_email \[ops\_tools\]\). Fix at shipgate.yaml\#action\_surface.actions\[tool='billing.cancel\_invoice\_email'\]. Context: 2 semantic evidence gap\(s\); scan results are not trustworthy enough to gate release.

Blockers (0): none

Review items (0): none

Evidence coverage: static (2/2 catalog tools reachable; 2 semantic evidence gap(s); 0/2 actions pass-eligible)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 0
- High: 0
- Medium: 0
- Low: 0
- Suppressed: 0
- Status: No release blockers detected (legacy; see Release Decision above)

## Top Findings

No critical or high findings.

## Finding Provenance

Reviewer triage signal only. Provenance kind does not change severity, release decision, fingerprints, baselines, or CI exit codes.

| Provenance kind | Active findings |
| --- | ---: |
| `static_declaration` | 0 |
| `ast_extraction` | 0 |
| `keyword_heuristic` | 0 |
| `regex_heuristic` | 0 |
| `policy_pack` | 0 |
| `runtime_trace` | 0 |

Suppressed findings excluded: 0

## Capability <-> Intent Diff

No capability/intent misalignments detected from static evidence.

## Recommended Next Actions

No action required from static findings.

## Tool Surface Summary

- Total tools: 2
- High-risk tools: 2
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: mcp=2

## Action Surface Diff

- Status: disabled - No action-surface comparison source was provided.
- Base: none

## Capability Runtime Evidence

No local runtime trace artifacts were declared for capability evidence.

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## Findings By Category

No findings.

## Agent Binding Surface

Status: declared
Root agent: none \(graph rooted by declared tool sources\)
Entry points: ops\_tools
Pass eligible: true
Catalog partition: 2 reachable, 0 possible, 0 unbound

## Appendix: Root-Reachable Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| support.delete\_case\_message | mcp | customer\_communication, destructive, external\_write | customer\_communication=high, destructive=medium, external\_write=high | \- | support-ops |
| billing.cancel\_invoice\_email | mcp | customer\_communication, destructive, external\_write, financial\_action | customer\_communication=high, destructive=medium, external\_write=high, financial\_action=high | \- | billing-platform |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
