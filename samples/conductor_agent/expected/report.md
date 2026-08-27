# Agents Shipgate Report

Project: conductor-agent
Agent: durable-order-agent
Target: local

## Release Decision

Decision: insufficient_evidence
Reason: Insufficient evidence: the agent's tool binding graph is incomplete \(durable\_order\_agent \[conductor\_workflows\]\). Fix at shipgate.yaml\#agent\_bindings. Context: 1 binding evidence gap\(s\), 1 semantic evidence gap\(s\), 1 low-confidence tool\(s\) and 4 source warning\(s\); scan results are not trustworthy enough to gate release.

Blockers (0): none

Review items (2):
- HIGH SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE — Conductor tool surface cannot be statically enumerated
- HIGH SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE — Conductor tool surface cannot be statically enumerated

Evidence coverage: mixed (1 low-confidence tool(s); 4 source warning(s); 1 binding evidence gap(s); 1/1 catalog tools reachable; 1 semantic evidence gap(s); 0/1 actions pass-eligible; human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 0
- High: 2
- Medium: 0
- Low: 0
- Suppressed: 0
- Status: Warnings detected (legacy; see Release Decision above)

## Top Findings

2 findings across 1 subject, most urgent first.

- durable-order-agent \(agent-wide\) — review \(2 high\)
  - high SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE — Conductor tool surface cannot be statically enumerated \(at workflows/order-agent.json\#/tasks/1\)
    - Use literal MCP server and method bindings, a static LLM tool advertisement, or an exact local sub-workflow target before release review.
  - high SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE — Conductor tool surface cannot be statically enumerated \(at workflows/order-agent.json\#/tasks/4/decisionCases/yes/0\)
    - Use literal MCP server and method bindings, a static LLM tool advertisement, or an exact local sub-workflow target before release review.

## Finding Provenance

Reviewer triage signal only. Provenance kind does not change severity, release decision, fingerprints, baselines, or CI exit codes.

| Provenance kind | Active findings |
| --- | ---: |
| `static_declaration` | 2 |
| `ast_extraction` | 0 |
| `keyword_heuristic` | 0 |
| `regex_heuristic` | 0 |
| `policy_pack` | 0 |
| `runtime_trace` | 0 |

Suppressed findings excluded: 0

## Capability <-> Intent Diff

Agent intent:

- declared\_purpose: review and execute selected order lookups through a Conductor OSS workflow (tags: none)

Actual capabilities:

- No high-risk or gap-referenced capabilities selected.

Policy/control gaps:

- HIGH undetected\_gap: Conductor tool surface cannot be statically enumerated. (at workflows/order-agent.json)
  Requires: Static review requires deterministic evidence for release gaps.
  Release implication: Human review is required to interpret this finding.
- HIGH undetected\_gap: Conductor tool surface cannot be statically enumerated. (at workflows/order-agent.json)
  Requires: Static review requires deterministic evidence for release gaps.
  Release implication: Human review is required to interpret this finding.

Release implication:

- Decision: insufficient\_evidence
- Static evidence is incomplete; capability/intent analysis may miss release-relevant signal — gather deeper sources before shipping.

Next validation:

- No additional validation scenarios suggested.

## Recommended Next Actions

- Use literal MCP server and method bindings, a static LLM tool advertisement, or an exact local sub-workflow target before release review.

## Source Warnings

- Conductor CALL\_MCP\_TOOL at workflows/order-agent.json\#/tasks/4/decisionCases/yes/0 has a dynamic or unresolved capability surface: \['method'\].
- Conductor LLM\_CHAT\_COMPLETE at workflows/order-agent.json\#/tasks/1 has a dynamic or unresolved capability surface: \['tools'\].
- Conductor capability 'HTTP' at workflows/order-agent.json\#/tasks/5 is recognized but not enumerated by the MCP-core v1 adapter.
- Conductor capability 'webSearch' at workflows/order-agent.json\#/tasks/1 is recognized but not enumerated by the MCP-core v1 adapter.

## Tool Surface Summary

- Total tools: 1
- High-risk tools: 0
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: conductor_mcp_call=1

## Action Surface Diff

- Status: disabled - No action-surface comparison source was provided.
- Base: none

## Capability Runtime Evidence

No local runtime trace artifacts were declared for capability evidence.

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## Conductor OSS Surface Summary

- Workflow files: 1
- Workflows: 1
- Tasks: 7
- LLM tasks: 1
- MCP discovery tasks: 1
- MCP call tasks: 2
- Human checkpoints: 1
- Structurally checkpointed MCP calls: 2
- Sub-workflow tasks: 0
- Dynamic or unresolved tool surfaces: 2
- Unsupported capabilities: 2

Conductor OSS warnings:

- Conductor CALL\_MCP\_TOOL at workflows/order-agent.json\#/tasks/4/decisionCases/yes/0 has a dynamic or unresolved capability surface: \['method'\].
- Conductor LLM\_CHAT\_COMPLETE at workflows/order-agent.json\#/tasks/1 has a dynamic or unresolved capability surface: \['tools'\].
- Conductor capability 'HTTP' at workflows/order-agent.json\#/tasks/5 is recognized but not enumerated by the MCP-core v1 adapter.
- Conductor capability 'webSearch' at workflows/order-agent.json\#/tasks/1 is recognized but not enumerated by the MCP-core v1 adapter.

## Findings By Category

### Conductor

- HIGH: SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE - Conductor tool surface cannot be statically enumerated
- HIGH: SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE - Conductor tool surface cannot be statically enumerated

## Agent Binding Surface

Status: partial
Root agent: durable\_order\_agent \[conductor\_workflows\]
Pass eligible: false
Catalog partition: 1 reachable, 0 possible, 0 unbound

Binding gaps:
- `partial\_binding\_evidence` — Conductor workflow tool bindings include dynamic or unresolved surfaces.

## Appendix: Root-Reachable Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| lookup\_order | conductor\_mcp\_call | read\_only | read\_only=medium | \- | \- |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
