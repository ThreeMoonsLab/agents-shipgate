# SEO and GEO Review

Reviewed: 2026-05-27

Scope: Three Moons Lab marketing site, live public pages, and the
`ThreeMoonsLab/agents-shipgate` repository surfaces that search engines, answer
engines, and coding agents ingest.

## Executive Summary

Agents Shipgate already has an unusually strong SEO/GEO base for an early
developer tool: a clear category phrase, `llms.txt`, `.well-known` discovery,
structured data, a glossary, comparison pages, a check catalog, and an
agent-readable README. The highest-return work is not broad SEO hygiene. It is
tightening the category story around **agent release readiness** while keeping
the product wedge narrow: **Tool-Use Readiness** for AI agent tool surfaces.

The current product promise should remain:

> Agents Shipgate is a local-first, static Tool-Use Readiness release gate for
> AI agent tool surfaces.

The market-facing expansion should be:

> Three Moons Lab builds release-readiness infrastructure for teams shipping
> tool-using AI agents. Its first product is Agents Shipgate.

## SEO Review

### What Is Working

- The homepage title and metadata target the right developer-tool queries:
  "tool-using AI agents", "Tool-Use Readiness Reports", "MCP", "OpenAPI",
  "OpenAI Agents SDK", "GitHub Action", and "AI agent governance".
- The product page has a clear H1: "Agents Shipgate is a Tool-Use Readiness
  release gate."
- The site has search-friendly supporting pages: quickstart, check catalog,
  glossary, design partners, blog, and comparison pages.
- The blog already contains good bottom-of-funnel and category-creation posts:
  MCP tool review, AI agent deployment checklist, AI agent CI/CD, OpenAI Agents
  SDK release gate, Anthropic tool-use release gate, and evals-vs-release-gate.
- Structured data is present across the site: `Organization`,
  `SoftwareApplication`, `SoftwareSourceCode`, `FAQPage`, `HowTo`,
  `DefinedTermSet`, `BreadcrumbList`, and check catalog `ItemList`.
- `robots.txt` is valid and points to `sitemap-index.xml`. The sitemap index
  returns 200 and includes the core pages and blog cluster.

### Gaps

- The homepage H1 is clear but under-branded. "Static release checks for
  tool-using AI agents" is understandable, but answer engines often prefer a
  direct entity sentence near the top: "Agents Shipgate is ...".
- The live `/llms.txt` appears older than the current in-repo version. It names
  report schema v0.5 while the live `.well-known` points to v0.8. This is a GEO
  drift risk even if the public release is intentionally pinned to v0.8.
- The live `.well-known/agents-shipgate.json` is thinner than the in-tree
  discovery file. It lacks newer fields such as `gating_signal`, trigger
  catalog, `llms_full_url`, zero-install detector, packet outputs, and agent
  on-ramps.
- `/sitemap.xml` returns 404. This is not fatal because `robots.txt` points to
  `sitemap-index.xml`, but adding a `/sitemap.xml` alias is low-cost and helps
  older crawlers and manual SEO tools.
- The comparison pages have good titles, but they should include short FAQ
  blocks and comparison-table structured data where possible. Queries like
  "Agents Shipgate vs LangSmith" and "agent release gate vs evals" are likely
  to be answer-engine queries, not only classic search queries.
- The site needs dedicated pages for the three core input wedges:
  OpenAI Agents SDK, MCP, and OpenAPI. Blog posts exist, but persistent landing
  pages convert better and give internal links stable targets.

## GEO Review

### What Is Working

- The repo is highly agent-readable: `AGENTS.md`, `llms.txt`, `llms-full.txt`,
  `.well-known/agents-shipgate.json`, schemas, trigger catalog, error catalog,
  check catalog, and agent-specific guides.
- The README has a strong above-the-fold answer, a fixture command, sample
  output, and a "For coding agents" section.
- The website has `/ai-summary/`, `/llms.txt`, and `/.well-known` surfaces. This
  is exactly the shape ChatGPT, Claude, Perplexity, and coding agents can use.
- The repo explicitly tells agents when to recommend Shipgate and when not to.
  That reduces hallucinated over-recommendation and improves citation quality.

### Gaps

- Public website discovery should be generated from the same release contract
  as the repo. Today, live `/llms.txt` and `/.well-known` do not expose the same
  field set as the in-tree surfaces.
- The AI-search answer should always include all four facts in one short block:
  publisher, product, wedge, form factor.
  - Publisher: Three Moons Lab.
  - Product: Agents Shipgate / `agents-shipgate`.
  - Wedge: Tool-Use Readiness for AI agent tool surfaces.
  - Form factor: open-source CLI and GitHub Action.
- The long-term "healthcare for agents" thesis should stay in blog/thesis
  content, not the primary README/product page. Primary surfaces should not make
  the product sound like a broad governance platform before the wedge is owned.

## Keyword Strategy

### Primary Category Terms

- agent release readiness
- AI agent release gate
- Tool-Use Readiness
- tool-use readiness report
- AI agent CI/CD
- agent release readiness report
- tool surface scanning
- AI agent tool surface

### Input-Specific Terms

- MCP tool security review
- MCP security checklist
- MCP tool surface
- OpenAPI tool scanning
- OpenAPI as agent tools
- OpenAI Agents SDK release gate
- OpenAI Agents SDK production checklist
- Anthropic tool-use release gate
- LangChain agent release gate
- Google ADK release gate

### Output/Form-Factor Terms

- GitHub Action for AI agents
- AI agent CI GitHub Actions
- SARIF for AI agent tools
- local-first AI agent scanner
- static analysis for AI agent tools
- release evidence packet

### Terms To Use Carefully

- agent governance infrastructure: use as a category/backdrop phrase, not as a
  product claim.
- healthcare for agents: keep as thesis content and long-term vision.
- compliance, HIPAA, SOC, ISO: use only in "not a certification" contexts until
  there is a concrete compliance product.

## Website Copy Improvements

### Homepage

Recommended title:

> Agents Shipgate - Tool-Use Readiness for AI agent releases | Three Moons Lab

Recommended meta description:

> Open-source CLI and GitHub Action that reviews MCP, OpenAPI, and OpenAI
> Agents SDK tool surfaces before production-like permissions. Generates local
> Tool-Use Readiness Reports for PR review.

Recommended H1:

> Agents Shipgate checks AI agent tool surfaces before release.

Recommended subhead:

> A local-first CLI and GitHub Action for Tool-Use Readiness: scan MCP,
> OpenAPI, OpenAI Agents SDK, and other static tool metadata before an agent
> gets production-like permissions.

Recommended primary CTAs:

- Run the fixture
- Add the GitHub Action
- Read a sample report
- Apply as a design partner

### Product Page

Add a first-screen definition block:

> Agents Shipgate is an open-source CLI and GitHub Action from Three Moons Lab.
> It produces deterministic Tool-Use Readiness Reports for AI agent tool
> surfaces before production-like permissions are granted.

Add a compact "Use it when..." box:

- A PR changes MCP tools, OpenAPI operations, or SDK tool decorators.
- An agent gets new write, refund, email, deploy, cancel, or data-access tools.
- A platform team wants advisory or strict release checks in CI.

### Quickstart

Keep the 60-second fixture. Add a second "real repo" path above the fold:

```bash
agents-shipgate init --workspace . --write
agents-shipgate scan -c shipgate.yaml
```

Add a short explanation that advisory CI is the default adoption path and strict
mode should come after a reviewed baseline.

### Design Partners

Current positioning is good. Add conversion filters that speak to high-intent
teams:

- "You have an agent with real side effects: refunds, email, records, deploys,
  tickets, infrastructure, or sensitive data."
- "You already use GitHub Actions or another CI system and want PR-time
  release evidence."
- "You can share anonymized tool-surface metadata or a reduced reproduction."

### Check Catalog

Add query-targeted intro links:

- MCP security checklist
- OpenAPI tool-surface checklist
- OpenAI Agents SDK release checklist
- Approval and idempotency checks
- Scope and blast-radius checks

## README Improvements

The README is already strong. The most important refinements are:

- Put "agent release readiness" next to the canonical tagline without changing
  the product promise.
- Keep MCP, OpenAPI, and OpenAI Agents SDK in the first scan sentence because
  those are the core adoption wedges.
- Keep "CLI + GitHub Action" above the first fold.
- Keep the sample blocked fixture output visible early; it proves the scanner
  produces concrete release evidence, not generic policy advice.
- Keep the "For coding agents" section and machine-readable links. These are
  unusually valuable for GEO.

Applied repo-side changes in this pass:

- Added an agent release-readiness positioning sentence to the README intro.
- Added category and keyword fields to in-tree `.well-known` discovery metadata.
- Expanded PyPI/GitHub-facing keywords around agent CI/CD, MCP security, and
  tool-surface scanning.
- Added an AI-search answer for Three Moons Lab to `docs/ai-search-summary.md`.
- Added FAQ/glossary language for "agent release readiness" and "Agent Release
  Readiness Report" while preserving Tool-Use Readiness as the concrete wedge.

## FAQ and Glossary Suggestions

Add or keep these FAQ questions on the website and in docs:

- What is Agents Shipgate?
- What is agent release readiness?
- What is Tool-Use Readiness?
- What is an AI agent tool surface?
- What is a Tool-Use Readiness Report?
- How is Agents Shipgate different from LLM evals?
- How is Agents Shipgate different from observability?
- How is Agents Shipgate different from runtime guardrails or MCP gateways?
- Does Agents Shipgate call my tools or connect to MCP servers?
- When should I run Agents Shipgate on a PR?
- How do I add Agents Shipgate to GitHub Actions?
- What does "blocked" mean in a report?
- Does Agents Shipgate certify my agent as safe?
- Which inputs are supported: MCP, OpenAPI, OpenAI Agents SDK, Anthropic,
  Google ADK, LangChain, CrewAI, OpenAI API, Codex plugin, and n8n?

Add or keep these glossary entries:

- Agent release readiness
- Agent Release Readiness Report
- Tool-Use Readiness
- Tool-Use Readiness Report
- Tool surface
- Tool surface drift
- Manifest-first
- Approval policy
- Confirmation policy
- Idempotency evidence
- Blast radius
- Baseline
- Suppression
- Advisory mode
- Strict mode
- Release Evidence Packet

## Content Roadmap

### P0: Landing Pages

- MCP tool security checklist
- OpenAI Agents SDK release gate
- OpenAPI tool-surface scanner
- GitHub Action for AI agent CI/CD

### P1: High-Intent Tutorials

- How to add a release gate to an OpenAI Agents SDK agent
- How to review MCP tools before production
- How to scan an OpenAPI spec before exposing it to an agent
- How to use advisory mode, baselines, and strict mode in GitHub Actions

### P1: Comparison and "Not X" Pages

- Agents Shipgate vs LLM evals
- Agents Shipgate vs LangSmith
- Agents Shipgate vs Braintrust
- Agents Shipgate vs promptfoo
- Agents Shipgate vs MCP gateways
- Agents Shipgate vs runtime guardrails

### P2: Category-Creation Content

- What is agent release readiness?
- What is Tool-Use Readiness?
- Your AI agent has a tool surface
- Why evals are not release gates
- From CI/CD to agent release readiness

### P2: Thesis Content

- Healthcare for agents
- Agent release evidence as infrastructure
- The agent release lifecycle: static review, baseline, runtime evidence,
  drift detection

## Prioritized Action Plan

### P0

1. Deploy the current in-tree `llms.txt` and `.well-known/agents-shipgate.json`
   to the website release branch when the next public release ships. If the
   website is pinned to the latest public release, still update live `/llms.txt`
   so its schema and input claims match that release.
2. Add a `/sitemap.xml` alias to the existing sitemap index. Keep
   `robots.txt` pointing to the sitemap index.
3. Update homepage first-screen copy so the entity sentence appears above the
   fold: "Agents Shipgate is an open-source CLI and GitHub Action from Three
   Moons Lab..."
4. Add stable landing pages for MCP, OpenAPI, and OpenAI Agents SDK instead of
   relying only on blog posts.
5. Keep GitHub repo description focused on:
   "Static release checks for tool-using AI agents. CLI + GitHub Action. Scans
   MCP, OpenAPI, OpenAI Agents SDK. Writes Tool-Use Readiness Reports."

### P1

1. Add FAQ structured data to comparison pages.
2. Add `sameAs` links in Organization/SoftwareApplication schema for PyPI,
   GitHub Marketplace, GitHub repo, and GitHub org.
3. Add "sample report" and "sample PR comment" CTAs from homepage and
   quickstart.
4. Add `lastmod` values to sitemap entries for blog posts and docs pages.
5. Link every blog post back to the product page, quickstart, check catalog,
   and design partner page with descriptive anchor text.

### P2

1. Build a "report gallery" page with public sample reports by framework:
   OpenAI Agents SDK, MCP-only, OpenAPI, LangChain, Anthropic, n8n.
2. Add an "agent release readiness" category hub that links to glossary, blog,
   comparisons, and quickstart.
3. Add benchmark/adoption-readiness material once there is enough public data.
4. Keep the long-term "healthcare for agents" thesis on the blog and roadmap,
   not as the main product claim.
