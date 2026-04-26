# Docs Index

A single entry point for human readers and AI agents walking the `docs/` tree.

## Concepts

- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`trust-model.md`](trust-model.md) — what the scanner does and doesn't do
- [`baseline.md`](baseline.md) — baseline workflow

## Reference

- [`checks.md`](checks.md) — full check catalog (human-readable)
- [`checks.json`](checks.json) — machine-readable check catalog (regenerated each release)
- [`manifest-v0.1.json`](manifest-v0.1.json) — JSON Schema for `shipgate.yaml`
- [`report-schema.v0.3.json`](report-schema.v0.3.json) — JSON Schema for `report.json`
- [`category.md`](category.md) — what an "agent release gate" is, in product terms

## Examples

- [`manifest-v0.1.example.minimal.yaml`](manifest-v0.1.example.minimal.yaml) — smallest valid manifest
- [`manifest-v0.1.example.full.yaml`](manifest-v0.1.example.full.yaml) — every section populated
- [`../samples/`](../samples/) — runnable fixtures
- [`../samples/_anti_patterns/`](../samples/_anti_patterns/) — manifests that intentionally fail validation

## Workflows

- [`integrations.md`](integrations.md) — CI/CD integration recipes (GHA, GitLab, CircleCI, Jenkins)
- [`troubleshooting.md`](troubleshooting.md) — error messages → fixes
- [`distribution.md`](distribution.md) — release process and SBOM/signature verification
- [`decisions.md`](decisions.md) — architectural decisions

## For agents

- [`../AGENTS.md`](../AGENTS.md) — agent-facing instructions
- [`../STABILITY.md`](../STABILITY.md) — what won't break across `0.x`
- [`../prompts/`](../prompts/) — reusable prompts
- [`../.well-known/agents-shipgate.json`](../.well-known/agents-shipgate.json) — discovery metadata
