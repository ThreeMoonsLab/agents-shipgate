# Docs Index

A single entry point for human readers and AI agents walking the `docs/` tree.

## Concepts

- [`overview.md`](overview.md) — one-page summary for developers, reviewers, and AI agents
- [`concepts.md`](concepts.md) — tool-use readiness in depth (the seven dimensions)
- [`category.md`](category.md) — what an "agent release gate" is, in product terms
- [`glossary.md`](glossary.md) — category vocabulary
- [`ai-search-summary.md`](ai-search-summary.md) — human-readable summary for AI search and coding agents
- [`design-partners.md`](design-partners.md) — early design partner criteria and contact path
- [`architecture.md`](architecture.md) — codebase layout for new contributors
- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`trust-model.md`](trust-model.md) — what the scanner does and doesn't do
- [`baseline.md`](baseline.md) — baseline workflow
- [`framework-adapter-checklist.md`](framework-adapter-checklist.md) — checklist for adding static framework adapters

## Reference

- [`checks.md`](checks.md) — full check catalog (human-readable)
- [`checks.json`](checks.json) — machine-readable check catalog (regenerated each release)
- [`manifest-v0.1.json`](manifest-v0.1.json) — JSON Schema for `shipgate.yaml`
- [`report-schema.v0.13.json`](report-schema.v0.13.json) — JSON Schema for `report.json` (current; emitted reports carry `report_schema_version: "0.13"`, which adds static Codex plugin surface metadata)
- [`agent-action-guide.md`](agent-action-guide.md) — per-category recipe for what to do with a finding (canonical fix per check category, last-resort suppression rules)
- [`upstream-integrations.md`](upstream-integrations.md) — per-framework 60-second drop-in for adding Shipgate to an existing project (OpenAI Agents SDK, LangChain, CrewAI, ADK, MCP-only, OpenAPI-only, OpenAI Messages API, Anthropic Messages API)
- [`report-schema.v0.12.json`](report-schema.v0.12.json) — frozen v0.12 reference schema; pre-v0.13 reports validate against this
- [`report-schema.v0.11.json`](report-schema.v0.11.json) — frozen v0.11 reference schema; pre-v0.12 reports validate against this
- [`report-schema.v0.10.json`](report-schema.v0.10.json) — frozen v0.10 reference schema; pre-v0.11 reports validate against this
- [`report-schema.v0.9.json`](report-schema.v0.9.json) — frozen v0.9 reference schema; pre-v0.10 reports validate against this
- [`report-schema.v0.8.json`](report-schema.v0.8.json) — frozen v0.8 reference schema; pre-v0.9 reports validate against this
- [`report-schema.v0.7.json`](report-schema.v0.7.json) — frozen v0.7 reference schema; pre-v0.8 reports validate against this
- [`report-schema.v0.6.json`](report-schema.v0.6.json) — frozen v0.6 reference schema; pre-v0.7 reports validate against this
- [`packet-schema.v0.3.json`](packet-schema.v0.3.json) — JSON Schema for the Release Evidence Packet
- [`category.md`](category.md) — what an "agent release gate" is, in product terms

## Examples

- [`examples.md`](examples.md) — narrative tour of sample agents and CI recipes
- [`../examples/golden-prs/`](../examples/golden-prs/) — end-to-end advisory PR examples for humans and coding agents
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
- [`agent-contract-current.md`](agent-contract-current.md) — current statement of which `report.json` fields agents and CI integrations should read
- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer for `report.json`; walks the file in the order a new consumer should read it
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may do mechanically vs. what must defer to a human reviewer
- [`autofix-policy.md`](autofix-policy.md) — which findings are safe to apply, which need review, and how `apply-patches --confidence` filters them
- [`diagnostics.md`](diagnostics.md) — ranked next-action diagnostics surfaced by `detect`, `doctor`, and structured-error JSON
- [`target-repo-agent-snippets.md`](target-repo-agent-snippets.md) — copyable `AGENTS.md`, `CLAUDE.md`, Cursor, PR template, and advisory workflow snippets for downstream repos
- [`agents/use-with-claude-code.md`](agents/use-with-claude-code.md) — install the `/shipgate` slash command and `agents-shipgate` skill in your agent project
- [`agents/use-with-codex.md`](agents/use-with-codex.md) — drop the canonical `AGENTS.md` snippet in for OpenAI Codex
- [`agents/use-with-cursor.md`](agents/use-with-cursor.md) — drop the auto-attach `.cursor/rules/agents-shipgate.mdc` rule in for Cursor
- [`agent-adoption-harness.md`](agent-adoption-harness.md) — manual protocol for measuring whether coding agents discover and use Shipgate
- [`minimal-real-configs.md`](minimal-real-configs.md) — framework-by-framework references to the smallest working manifest
- [`../AGENTS.md`](../AGENTS.md) — agent-facing instructions
- [`../CLAUDE.md`](../CLAUDE.md) — Claude Code-specific notes
- [`../STABILITY.md`](../STABILITY.md) — what won't break across `0.x`
- [`../prompts/`](../prompts/) — reusable prompts
- [`../llms.txt`](../llms.txt) — AI-readable project summary
- [`ai-search-summary.md`](ai-search-summary.md) — prose companion to `llms.txt`
- [`../.well-known/agents-shipgate.json`](../.well-known/agents-shipgate.json) — discovery metadata
