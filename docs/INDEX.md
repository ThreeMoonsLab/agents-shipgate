# Docs Index

A single entry point for human readers and AI agents walking the `docs/` tree.

## Concepts

- [`overview.md`](overview.md) — one-page summary for developers, reviewers, and AI agents
- [`concepts.md`](concepts.md) — tool-use readiness in depth (the seven dimensions)
- [`category.md`](category.md) — what an "agent release gate" is, in product terms
- [`glossary.md`](glossary.md) — category vocabulary
- [`architecture.md`](architecture.md) — codebase layout for new contributors
- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`trust-model.md`](trust-model.md) — what the scanner does and doesn't do
- [`baseline.md`](baseline.md) — baseline workflow
- [`framework-adapter-checklist.md`](framework-adapter-checklist.md) — checklist for adding static framework adapters

## Reference

- [`checks.md`](checks.md) — full check catalog (human-readable)
- [`checks.json`](checks.json) — machine-readable check catalog (regenerated each release)
- [`manifest-v0.1.json`](manifest-v0.1.json) — JSON Schema for `shipgate.yaml`
- [`report-schema.v0.7.json`](report-schema.v0.7.json) — JSON Schema for `report.json` (current; emitted reports carry `report_schema_version: "0.7"`)
- [`report-schema.v0.6.json`](report-schema.v0.6.json) — frozen v0.6 reference schema; pre-v0.7 reports validate against this
- [`category.md`](category.md) — what an "agent release gate" is, in product terms

## Examples

- [`examples.md`](examples.md) — narrative tour of sample agents and CI recipes
- [`manifest-v0.1.example.minimal.yaml`](manifest-v0.1.example.minimal.yaml) — smallest valid manifest
- [`manifest-v0.1.example.full.yaml`](manifest-v0.1.example.full.yaml) — every section populated
- [`../samples/`](../samples/) — runnable fixtures
- [`../samples/_anti_patterns/`](../samples/_anti_patterns/) — manifests that intentionally fail validation

## Workflows

- [`quickstart.md`](quickstart.md) — 60-second install + first scan
- [`faq.md`](faq.md) — common questions, AI-search-friendly
- [`integrations.md`](integrations.md) — CI/CD integration recipes (GitHub Actions, GitLab CI, CircleCI, Jenkins snippet)
- [`troubleshooting.md`](troubleshooting.md) — error messages → fixes
- [`distribution.md`](distribution.md) — release process and SBOM/signature verification
- [`decisions.md`](decisions.md) — architectural decisions

## For agents

- [`agent-recipes.md`](agent-recipes.md) — copy-pasteable AI-agent workflows for the canonical 4-call flow (`detect → init → scan → apply-patches`)
- [`minimal-real-configs.md`](minimal-real-configs.md) — framework-by-framework references to the smallest working manifest
- [`../AGENTS.md`](../AGENTS.md) — agent-facing instructions
- [`../CLAUDE.md`](../CLAUDE.md) — Claude Code-specific notes
- [`../STABILITY.md`](../STABILITY.md) — what won't break across `0.x`
- [`../prompts/`](../prompts/) — reusable prompts
- [`../llms.txt`](../llms.txt) — AI-readable project summary
- [`../.well-known/agents-shipgate.json`](../.well-known/agents-shipgate.json) — discovery metadata
