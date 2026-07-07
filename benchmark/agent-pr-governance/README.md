# AgentPR Governance Case Catalog

This is the product-hardening governance benchmark and case catalog for
AI-generated agent PRs. The executable subset materializes deterministic
base/head repos, runs the real verifier, builds base/head capability locks, and
asserts capability-level semantic deltas. Catalog-only and external-evidence
rows remain visible backlog; they are not scored by default.

It is separate from the adoption benchmark: adoption asks whether coding agents
discover and run Agents Shipgate; governance asks whether the verifier should
prevent unsafe merge, route authority gaps to humans, and give reviewers enough
evidence.

The case catalog is [`cases.yaml`](cases.yaml). Cases are intentionally small
and deterministic. They name the changed capability, the expected
`release_decision.decision`, the expected `merge_verdict`, required evidence,
the safe next actor, status, fixture id, metric membership, and optional
`CapabilityFactV1` / lock-diff expectations.

Run the executable subset:

```bash
python scripts/run_governance_benchmark.py \
  --catalog benchmark/agent-pr-governance/cases.yaml --json
```

Include non-executable backlog rows as skipped results:

```bash
python scripts/run_governance_benchmark.py \
  --catalog benchmark/agent-pr-governance/cases.yaml \
  --include-catalog-only --json
```

The runner lives in `scripts/`; benchmark orchestration does not ship inside
`src/agents_shipgate`. Git fixture materialization reuses the existing audited
fixture helper instead of carrying a benchmark-specific subprocess copy.
Result JSON has no wall-clock timestamp and is deterministic for the same
catalog, fixtures, scanner version, and local git behavior.

## Metrics

The runner computes these acceptance metrics:

- unsafe merge prevention: unsafe cases must not produce `mergeable`.
- safe pass rate: benign controls should produce `mergeable` or clear
  not-applicable routing.
- authority routing: human-only gaps must route to `human`, not `coding_agent`.
- explanation usefulness: `capability_review.top_changes[]` or
  `release_decision.review_items[]` must point at the changed capability. This
  v0 signal is intentionally coarse and should not be treated as proof of
  reviewer-quality prose.
- remediation boundary: mechanical fixes may be agent-routable; approval,
  idempotency, broad-scope, waiver, baseline, and trust-root gaps may not.
- capability semantic fidelity: declared `CapabilityFactV1` / lock-diff
  expectations must match the actual capability diff.

## Adding A Case

Add a case only when it covers core product behavior or design-partner feedback
exposes a real false positive, missed capability, confusing next action, or
unsafe-pass risk. Do not add cases for academic breadth alone.

Every case must name its status and the minimum artifact set needed to
reproduce it:
`pr_diff`, `shipgate_manifest`, `tool_source`, `agent_trace`, `policy_pack`,
`verifier_json`, or `human_review_note`.

Executable cases must also name a deterministic fixture builder. Capability
assertions should prefer semantic selectors (`tool_name`, `effect`,
`semantic_direction`, `changed_hashes`, and scope/risk predicates) over source
line/path assertions.

## Replay closure

The `executable` cases are not just a catalog — they are replayed through the
live verifier and pinned to a committed baseline, so a verdict regression turns
CI red:

```bash
PYTHONPATH=src python scripts/run_governance_benchmark.py --json          # run all executable cases
PYTHONPATH=src python scripts/run_governance_benchmark.py \
  --out benchmark/agent-pr-governance/results/baseline.v0.2.json          # regenerate the baseline
```

- [`results/baseline.v0.2.json`](results/baseline.v0.2.json) — the committed,
  deterministic result (byte-stable: no wall-clock fields, repo-relative
  `catalog_path`). The only tracked file under `results/`; ad-hoc runs stay
  gitignored.
- `tests/test_governance_benchmark.py` replays every executable case live on
  each CI run and asserts the verdicts, metric totals, and capability
  expectations.
- `tests/test_governance_benchmark_baseline.py` asserts a fresh run reproduces
  the committed baseline byte-for-byte — any intended verdict change must
  regenerate the baseline in the same commit.
