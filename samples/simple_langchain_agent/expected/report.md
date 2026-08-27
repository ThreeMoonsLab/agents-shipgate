# Agents Shipgate Report

Project: simple-langchain-agent
Agent: support-case-reader
Target: local

## Release Decision

Decision: passed
Reason: All in-scope actions have complete, conflict-free explicit or structural static effect and authority evidence; no active blockers. Runtime behavior was not verified.

Blockers (0): none

Review items (0): none

Evidence coverage: static (2/2 catalog tools reachable; 2/2 actions pass-eligible)

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
- High-risk tools: 0
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: langchain_inventory=2

## Action Surface Diff

- Status: disabled - No action-surface comparison source was provided.
- Base: none

## Capability Runtime Evidence

No local runtime trace artifacts were declared for capability evidence.

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## LangChain Surface Summary

- Python entrypoints: 1
- Function tools: 1
- Structured tools: 1
- Tool nodes: 0
- Agent tool bindings: 1
- Dynamic or unresolved tool surfaces: 0
- Tool inventory files: 1

## Findings By Category

No findings.

## Agent Binding Surface

Status: structural
Root agent: agent\_v1:e72499e3feb23dae6e706766
Entry points: agent \[langchain\_agent\]
Pass eligible: true
Catalog partition: 2 reachable, 0 possible, 0 unbound

## Appendix: Root-Reachable Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| summarize\_case | langchain\_inventory | read\_only | read\_only=high | \- | \- |
| lookup\_case | langchain\_inventory | read\_only | read\_only=high | \- | \- |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
