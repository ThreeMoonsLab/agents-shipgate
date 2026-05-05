# Agents Shipgate Report

Project: simple-langchain-agent
Agent: support-case-reader
Target: local

## Release Decision

Decision: review_required
Reason: Static-only scan with low-confidence evidence; human review recommended.

Blockers (0): none

Review items (0): none

Evidence coverage: mixed (2 low-confidence tool(s); human review recommended)

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

## Recommended Next Actions

No action required from static findings.

## Tool Surface Summary

- Total tools: 2
- High-risk tools: 0
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: langchain_function=1, langchain_structured_tool=1

## LangChain Surface Summary

- Python entrypoints: 1
- Function tools: 1
- Structured tools: 1
- Tool nodes: 0
- Agent tool bindings: 1
- Dynamic or unresolved tool surfaces: 0
- Tool inventory files: 0

## Findings By Category

No findings.

## Appendix: Normalized Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| lookup\_case | langchain\_function | read\_only | read\_only=medium | \- | \- |
| summarize\_case | langchain\_structured\_tool | \- | \- | \- | \- |


## Disclaimer

Agents Shipgate is an advisory release-readiness scanner. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
