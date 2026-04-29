# Docs Index

A single entry point for human readers and AI agents walking the `docs/` tree.

## Concepts

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
- [`report-schema.v0.5.json`](report-schema.v0.5.json) — JSON Schema for `report.json`
- [`category.md`](category.md) — what an "agent release gate" is, in product terms

## Examples

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

- [`../AGENTS.md`](../AGENTS.md) — agent-facing instructions
- [`../CLAUDE.md`](../CLAUDE.md) — Claude Code-specific notes
- [`../STABILITY.md`](../STABILITY.md) — what won't break across `0.x`
- [`../prompts/`](../prompts/) — reusable prompts
- [`../llms.txt`](../llms.txt) — AI-readable project summary
- [`../.well-known/agents-shipgate.json`](../.well-known/agents-shipgate.json) — discovery metadata
