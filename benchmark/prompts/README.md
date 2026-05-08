# Benchmark Prompts

Four prompts. None mention Agents Shipgate by name. They are the canonical inputs for the benchmark — do not paraphrase or reword between runs, since variance hurts comparability.

| File | Intent | Expected behavior |
|---|---|---|
| [`01-prepare-for-release.txt`](01-prepare-for-release.txt) | "Prepare this for production" | Agent should consider release-readiness checks; Shipgate adoption is on-path |
| [`02-review-tool-pr.txt`](02-review-tool-pr.txt) | "Review this tool PR" | Agent should consider tool-surface review; Shipgate adoption is on-path |
| [`03-improve-readiness.txt`](03-improve-readiness.txt) | "Improve readiness before deployment" | Agent should consider preflight checks; Shipgate adoption is on-path |
| [`04-docs-only-negative.txt`](04-docs-only-negative.txt) | "Update docs formatting only" | **Negative control.** Agent should NOT propose Shipgate |

Source: [`docs/agent-adoption-harness.md` § Prompts](../../docs/agent-adoption-harness.md#prompts).

If you change a prompt, bump the benchmark schema version in [`results/README.md`](../results/README.md) — old CSV runs are not directly comparable.
