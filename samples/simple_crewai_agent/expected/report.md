# Agents Shipgate Report

Project: simple-crewai-agent
Agent: support-case-crew
Target: local

## Release Decision

Decision: review_required
Reason: 1 finding need review and evidence coverage is incomplete.

Blockers (0): none

Review items (1):
- HIGH SHIP-AUTH-MISSING-SCOPE — FileReadTool lacks declared auth scopes

Evidence coverage: static (4 source warning(s); 1 semantic review concern(s); 2/3 actions pass-eligible; human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 0
- High: 1
- Medium: 0
- Low: 0
- Suppressed: 0
- Status: Warnings detected (legacy; see Release Decision above)

## Top Findings

1. FileReadTool lacks declared auth scopes
   Evidence: risk\_tags=\['read\_only'\]
   Recommendation: Declare operation-specific auth scopes for FileReadTool, or explicitly declare anonymous authority when the operation requires no credentials.

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

- declared\_purpose: look up and summarize read-only support case metadata (tags: none)

Actual capabilities:

- FileReadTool: capability=read\_only, risk=read\_only, control=partial

Policy/control gaps:

- HIGH scope\_drift \[FileReadTool\]: FileReadTool lacks declared auth scopes. (at inventories/tools.json)
  Requires: Scope-requiring tools must declare operation-specific auth scopes.
  Release implication: Release reviewers cannot assess least privilege.

Release implication:

- Decision: review\_required
- 1 release-relevant finding\(s\) require release review before shipping.

Next validation:

- Least-privilege scope review: Manifest and tool scopes match the narrow permissions needed for the release.

## Recommended Next Actions

- Declare operation-specific auth scopes for FileReadTool, or explicitly declare anonymous authority when the operation requires no credentials.

## Source Warnings

- CrewAI prebuilt tool 'FileReadTool' at crew.py:28 was recorded as low-confidence metadata; provide an explicit inventory for full review.
- Duplicate tool name 'lookup\_case'; kept crewai\_inventory source 'crewai\_inventory:inventories/tools.json' and merged metadata from crewai\_class\_tool source 'crewai\_agent'.
- Duplicate tool name 'summarize\_case'; kept crewai\_inventory source 'crewai\_inventory:inventories/tools.json' and merged metadata from crewai\_function source 'crewai\_agent'.
- Duplicate tool name 'FileReadTool'; kept crewai\_inventory source 'crewai\_inventory:inventories/tools.json' and merged metadata from crewai\_prebuilt\_tool source 'crewai\_agent'.

## Tool Surface Summary

- Total tools: 3
- High-risk tools: 0
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: crewai_inventory=3

## Action Surface Diff

- Status: disabled - No action-surface comparison source was provided.
- Base: none

## Capability Runtime Evidence

No local runtime trace artifacts were declared for capability evidence.

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## CrewAI Surface Summary

- Python entrypoints: 1
- Agents: 1
- Crews: 1
- Function tools: 1
- Class tools: 1
- Prebuilt tools: 1
- Dynamic or unresolved tool surfaces: 0
- Tool inventory files: 1

CrewAI warnings:

- CrewAI prebuilt tool 'FileReadTool' at crew.py:28 was recorded as low-confidence metadata; provide an explicit inventory for full review.

## Findings By Category

### Auth

- HIGH: SHIP-AUTH-MISSING-SCOPE [FileReadTool] - FileReadTool lacks declared auth scopes

## Appendix: Normalized Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| FileReadTool | crewai\_inventory | read\_only | read\_only=high | \- | \- |
| lookup\_case | crewai\_inventory | read\_only | read\_only=high | \- | \- |
| summarize\_case | crewai\_inventory | read\_only | read\_only=high | \- | \- |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
