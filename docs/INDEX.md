# Docs Index

A single entry point for human readers and AI agents walking the `docs/` tree.

## Concepts

- [`overview.md`](overview.md) — one-page summary for developers, reviewers, and AI agents
- [`concepts.md`](concepts.md) — tool-use readiness in depth (the seven dimensions)
- [`category.md`](category.md) — what an "agent release gate" is, in product terms
- [`glossary.md`](glossary.md) — category vocabulary
- [`ai-search-summary.md`](ai-search-summary.md) — human-readable summary for AI search and coding agents
- [`design-partners.md`](design-partners.md) — early design partner criteria and contact path
- [`design-partner-verifier-pilot.md`](design-partner-verifier-pilot.md) — runbook for design partners bringing one AI-generated agent PR through the verifier loop
- [`architecture.md`](architecture.md) — codebase layout for new contributors
- [`engineering/ai-coding-workflow-verifier.md`](engineering/ai-coding-workflow-verifier.md) — canonical engineering guide and roadmap for making Agents Shipgate the deterministic verifier inside AI coding workflows
- [`agent-native-merge-contract.md`](agent-native-merge-contract.md) — the agent-native protocol map: the eight merge contracts, each mapped to the artifact that implements it
- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`trust-model.md`](trust-model.md) — what the scanner does and doesn't do
- [`baseline.md`](baseline.md) — baseline workflow
- [`framework-adapter-checklist.md`](framework-adapter-checklist.md) — checklist for adding static framework adapters

## Reference

- [`checks.md`](checks.md) — full check catalog (human-readable)
- [`checks.json`](checks.json) — machine-readable check catalog (regenerated each release)
- [`manifest-v0.1.json`](manifest-v0.1.json) — JSON Schema for `shipgate.yaml`
- [`report-schema.v0.22.json`](report-schema.v0.22.json) — JSON Schema for `report.json` (current; emitted reports carry `report_schema_version: "0.22"`, adding the verifier-cycle top-level blocks `capability_change`, `protected_surface_changes`, `effective_policy`, `human_ack`, and `verifier_summary` alongside v0.21's `heuristics_filter` envelope)
- [`verifier-schema.v0.1.json`](verifier-schema.v0.1.json) — JSON Schema for `verifier.json` emitted by `agents-shipgate verify`
- [`attestation-schema.v0.1.json`](attestation-schema.v0.1.json) — JSON Schema for `attestation.json` emitted by `agents-shipgate attest`
- [`scenario-schema.v0.1.json`](scenario-schema.v0.1.json) — JSON Schema for the workflow-evidence `scenario.json` emitted by `agents-shipgate feedback capture`
- [`privacy.md`](privacy.md), [`terms.md`](terms.md), and [`report-sensitive-fields.json`](report-sensitive-fields.json) — Codex plugin privacy/terms, redaction behavior, and report sensitive-field inventory
- [`agent-action-guide.md`](agent-action-guide.md) — per-category recipe for what to do with a finding (canonical fix per check category, last-resort suppression rules)
- [`upstream-integrations.md`](upstream-integrations.md) — per-framework 60-second drop-in for adding Shipgate to an existing project (OpenAI Agents SDK, LangChain, CrewAI, ADK, MCP-only, OpenAPI-only, OpenAI Messages API, Anthropic Messages API)
- [`report-schema.v0.21.json`](report-schema.v0.21.json) — frozen v0.21 reference schema; pre-v0.22 reports validate against this
- [`report-schema.v0.20.json`](report-schema.v0.20.json) — frozen v0.20 reference schema; pre-v0.21 reports validate against this
- [`report-schema.v0.19.json`](report-schema.v0.19.json) — frozen v0.19 reference schema; pre-v0.20 reports validate against this
- [`report-schema.v0.18.json`](report-schema.v0.18.json) — frozen v0.18 reference schema; pre-v0.19 reports validate against this
- [`report-schema.v0.17.json`](report-schema.v0.17.json) — frozen v0.17 reference schema; pre-v0.18 reports validate against this
- [`report-schema.v0.16.json`](report-schema.v0.16.json) — frozen v0.16 reference schema; pre-v0.17 reports validate against this
- [`report-schema.v0.15.json`](report-schema.v0.15.json) — frozen v0.15 reference schema; pre-v0.16 reports validate against this
- [`report-schema.v0.14.json`](report-schema.v0.14.json) — frozen v0.14 reference schema; pre-v0.15 reports validate against this
- [`report-schema.v0.13.json`](report-schema.v0.13.json) — frozen v0.13 reference schema; pre-v0.14 reports validate against this
- [`report-schema.v0.12.json`](report-schema.v0.12.json) — frozen v0.12 reference schema; pre-v0.13 reports validate against this
- [`report-schema.v0.11.json`](report-schema.v0.11.json) — frozen v0.11 reference schema; pre-v0.12 reports validate against this
- [`report-schema.v0.10.json`](report-schema.v0.10.json) — frozen v0.10 reference schema; pre-v0.11 reports validate against this
- [`report-schema.v0.9.json`](report-schema.v0.9.json) — frozen v0.9 reference schema; pre-v0.10 reports validate against this
- [`report-schema.v0.8.json`](report-schema.v0.8.json) — frozen v0.8 reference schema; pre-v0.9 reports validate against this
- [`report-schema.v0.7.json`](report-schema.v0.7.json) — frozen v0.7 reference schema; pre-v0.8 reports validate against this
- [`report-schema.v0.6.json`](report-schema.v0.6.json) — frozen v0.6 reference schema; pre-v0.7 reports validate against this
- [`packet-schema.v0.6.json`](packet-schema.v0.6.json) — JSON Schema for the Release Evidence Packet (current; emitted packets carry `packet_schema_version: "0.6"`, adding the top-level `evidence_matrix` section (PR #104) and `ReleaseDecisionItem.{source, policy_evidence_source}` for reviewer-grade dual-source provenance (PR #103) on top of v0.5)
- [`packet-schema.v0.5.json`](packet-schema.v0.5.json) — frozen v0.5 reference packet schema; pre-v0.6 packets validate against this
- [`packet-schema.v0.4.json`](packet-schema.v0.4.json) — frozen v0.4 reference packet schema
- [`packet-schema.v0.3.json`](packet-schema.v0.3.json) — frozen v0.3 reference packet schema
- [`category.md`](category.md) — what an "agent release gate" is, in product terms

## Examples

- [`examples.md`](examples.md) — narrative tour of sample agents and CI recipes
- [`../examples/golden-prs/`](../examples/golden-prs/) — end-to-end advisory PR examples for humans and coding agents
- [`manifest-v0.1.example.minimal.yaml`](manifest-v0.1.example.minimal.yaml) — smallest valid manifest
- [`manifest-v0.1.example.full.yaml`](manifest-v0.1.example.full.yaml) — every section populated
- [`../samples/`](../samples/) — runnable fixtures
- [`../samples/_anti_patterns/`](../samples/_anti_patterns/) — manifests that intentionally fail validation

## Workflows

- [`quickstart.md`](quickstart.md) — verify-first AI-generated PR workflow
- [`faq.md`](faq.md) — common questions, AI-search-friendly
- [`integrations.md`](integrations.md) — CI/CD integration recipes (GitHub Actions, GitLab CI, CircleCI, Jenkins snippet)
- [`troubleshooting.md`](troubleshooting.md) — error messages → fixes
- [`distribution.md`](distribution.md) — release process and SBOM/signature verification
- [`decisions.md`](decisions.md) — architectural decisions

## For agents

- [`agent-recipes.md`](agent-recipes.md) — copy-pasteable AI-agent workflows for verify-first PRs and first adoption (`detect → init → scan → apply-patches`)
- [`agent-contract-current.md`](agent-contract-current.md) — current statement of which `report.json` fields agents and CI integrations should read
- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer for `report.json`; walks the file in the order a new consumer should read it
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may do mechanically vs. what must defer to a human reviewer
- [`autofix-policy.md`](autofix-policy.md) — which findings are safe to apply, which need review, and how `apply-patches --confidence` filters them
- [`diagnostics.md`](diagnostics.md) — ranked next-action diagnostics surfaced by `detect`, `doctor`, and structured-error JSON
- [`target-repo-agent-snippets.md`](target-repo-agent-snippets.md) — copyable `AGENTS.md`, Codex skill, `CLAUDE.md`, Cursor, PR template, and advisory workflow snippets for downstream repos
- [`agents/use-with-claude-code.md`](agents/use-with-claude-code.md) — install the `/shipgate` slash command and `agents-shipgate` skill in your agent project
- [`agents/use-with-codex.md`](agents/use-with-codex.md) — install the canonical `AGENTS.md` snippet and repo-scoped Codex skill
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
