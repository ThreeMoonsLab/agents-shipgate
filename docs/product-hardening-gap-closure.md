# Product Hardening Gap Closure

This page is the implementation map for the major gaps identified during the
whole-project review. It is intentionally product-hardening oriented: every
item below either adds a repo artifact that can be checked in CI, or records a
non-goal boundary that prevents the verifier product from widening into a
runtime system.

## Closure Map

| Gap | Closure artifact | Completion evidence |
|---|---|---|
| Root repo did not dogfood Agents Shipgate | Root [`../shipgate.yaml`](../shipgate.yaml) and advisory [`../.github/workflows/agents-shipgate.yml`](../.github/workflows/agents-shipgate.yml) | `agents-shipgate doctor --config shipgate.yaml --json`; `agents-shipgate scan -c shipgate.yaml --format json` |
| Adoption benchmark was not a governance acceptance spec | [`../benchmark/agent-pr-governance/`](../benchmark/agent-pr-governance/) defines the AgentPR Governance case catalog / acceptance spec; it is not an executable benchmark yet | `tests/test_product_hardening_gap_closure.py` validates the catalog shape, category coverage, and decision coverage |
| Policy packs had matcher support but no test/explanation guidance | [`policy-packs.md`](policy-packs.md) documents positive/negative fixture tests and release-decision contribution-rule explanations | `tests/test_policy_packs.py`; `tests/test_product_hardening_gap_closure.py` |
| Trace/provenance did not have a unified local event and replay-bundle contract | [`agent-workflow-evidence.md`](agent-workflow-evidence.md), [`agent-trace-event-schema.v0.1.json`](agent-trace-event-schema.v0.1.json), and [`agent-workflow-evidence-bundle-schema.v0.1.json`](agent-workflow-evidence-bundle-schema.v0.1.json) define no-chain-of-thought evidence shapes | `tests/test_product_hardening_gap_closure.py` validates the schema contracts |
| Runtime inventory was only design-only and could blur the static boundary | [`runtime-inventory.md`](runtime-inventory.md) records the opt-in-only boundary and prohibits use from default scan/verify | `tests/test_product_hardening_gap_closure.py` checks the boundary wording |

## Product Invariants

- `report.json.release_decision.decision` remains the only release gate.
- `merge_verdict` and `can_merge_without_human` remain verifier/controller projections.
- The default path remains local-first and static: no agent execution, no LLM calls, no MCP connections, no tool calls, no network, and no telemetry.
- New case-catalog, trace, and runtime-inventory artifacts do not introduce a schema bump or a second verdict.

## Ongoing Rule

When design-partner feedback exposes a false positive, missed capability, or
unsafe-pass risk, add the smallest reproducible artifact to the governance case
catalog first. Add runtime execution, live inventory, or hosted workflow only
behind an explicit opt-in command and after documenting its trust boundary.
