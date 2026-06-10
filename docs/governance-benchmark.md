# Governance Benchmark

The AgentPR governance benchmark is a stable research asset for evaluating how
Agents Shipgate handles agent capability changes. It is not a product gate and
does not change `scan`, `verify`, GitHub Action outputs, policy-pack behavior,
or release decisions.

`release_decision.decision` remains the only release gate.

## Current Versions

- Catalog schema: [`governance-benchmark-catalog-schema.v0.2.json`](governance-benchmark-catalog-schema.v0.2.json)
- Result schema: [`governance-benchmark-result-schema.v0.2.json`](governance-benchmark-result-schema.v0.2.json)
- Canonical catalog: [`../benchmark/agent-pr-governance/cases.yaml`](../benchmark/agent-pr-governance/cases.yaml)

## Run

```bash
PYTHONPATH=src python scripts/run_governance_benchmark.py \
  --catalog benchmark/agent-pr-governance/cases.yaml --json
```

Useful filters:

```bash
PYTHONPATH=src python scripts/run_governance_benchmark.py --case mcp-safe-read-tool-added --json
PYTHONPATH=src python scripts/run_governance_benchmark.py --category unsafe_additions --json
PYTHONPATH=src python scripts/run_governance_benchmark.py --include-catalog-only --json
```

The runner emits deterministic JSON with no wall-clock timestamp by default.
Optional result files belong under `benchmark/agent-pr-governance/results/`,
which is ignored by git.

## Case Statuses

- `executable` — materialized into local base/head fixtures and scored.
- `catalog_only` — visible backlog or taxonomy entry; not scored by default.
- `external_evidence` — requires outside evidence or integrations not bundled
  into the local deterministic runner; not scored by default.

Selecting a non-executable case explicitly includes it as a skipped result
unless `--strict` is set.

## Metrics

- `unsafe_merge_prevention` — unsafe executable cases must not be mergeable.
- `safe_pass_rate` — benign reductions and safe read-only changes should be
  mergeable.
- `authority_routing` — authority or trust gaps should route to a human.
- `explanation_usefulness` — blocker/review evidence or top changes should
  identify the relevant capability.
- `remediation_boundary` — human-only governance gaps should not be assigned as
  safe coding-agent fixes.
- `capability_semantic_fidelity` — declared capability fact and semantic-delta
  expectations must match the actual capability lock diff.

## Capability Assertions

Executable cases can assert capability substrate behavior over
`CapabilityFactV1` and capability lock diff rows. Selectors include tool name,
provider, operation, effect, scope, broad scope, risk tags, changed hashes,
semantic direction, and semantic-change fields.

The benchmark uses the real verifier path plus capability lock export/diff.
That keeps the eval close to the shipped product while keeping all orchestration
in `scripts/`, outside the packaged scanner trust surface.

## What It Does Not Prove

The benchmark does not prove runtime enforcement, prompt robustness, model
correctness, or adversarial resistance. It does not execute agents, call tools,
connect to MCP servers, make network requests, or collect telemetry. It scores
deterministic static governance behavior over curated fixtures.

When comparing results across runs or tools, cite:

- the Agents Shipgate version,
- the catalog schema and result schema versions,
- the catalog commit,
- the selected case ids or categories,
- whether `--strict` or `--include-catalog-only` was used.
