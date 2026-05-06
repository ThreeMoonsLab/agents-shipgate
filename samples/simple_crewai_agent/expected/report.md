# Agents Shipgate Report

Project: simple-crewai-agent
Agent: support-case-crew
Target: local

## Release Decision

Decision: review_required
Reason: Static-only scan with low-confidence evidence; human review recommended.

Blockers (0): none

Review items (0): none

Evidence coverage: mixed (3 low-confidence tool(s); 1 source warning(s); human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 0
- High: 0
- Medium: 0
- Low: 0
- Suppressed: 0
- Status: Human review recommended (legacy; see Release Decision above)

## Top Findings

No critical or high findings.

## Capability <-> Intent Diff

No capability/intent misalignments detected from static evidence.

## Recommended Next Actions

No action required from static findings.

## Source Warnings

- CrewAI prebuilt tool 'FileReadTool' at crew.py:28 was recorded as low-confidence metadata; provide an explicit inventory for full review.

## Tool Surface Summary

- Total tools: 3
- High-risk tools: 0
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: crewai_class_tool=1, crewai_function=1, crewai_prebuilt_tool=1

## CrewAI Surface Summary

- Python entrypoints: 1
- Agents: 1
- Crews: 1
- Function tools: 1
- Class tools: 1
- Prebuilt tools: 1
- Dynamic or unresolved tool surfaces: 0
- Tool inventory files: 0

CrewAI warnings:

- CrewAI prebuilt tool 'FileReadTool' at crew.py:28 was recorded as low-confidence metadata; provide an explicit inventory for full review.

## Findings By Category

No findings.

## Appendix: Normalized Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| FileReadTool | crewai\_prebuilt\_tool | \- | \- | \- | \- |
| lookup\_case | crewai\_class\_tool | read\_only | read\_only=medium | \- | \- |
| summarize\_case | crewai\_function | \- | \- | \- | \- |


## Disclaimer

Agents Shipgate is an advisory release-readiness scanner. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
