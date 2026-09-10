# Beta strata inventory

The separate source register for [#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512),
started against main `404e41325e62a9d71ca3c5bbd8e23711197117a3` on 2026-09-10.
[`beta-strata-inventory.csv`](beta-strata-inventory.csv) uses the existing
[Cut A columns and vocabulary](strata-inventory.md#columns). The production
policy in `production_safety_requirements()` supplies its 21 cells and their
80-case allocation. The pre-1.0 inventory and its historical rounds stay separate.

**This is sourcing, not a beta corpus or release evidence.** There are no human
labels, frozen splits, admitted packets, receipts or qualifying results here.
`pinned` means the source revision is resolved; it does **not** mean ready for
human labeling. Never pass this register, its CSV, the miner worksheet or the
pre-1.0 labels to a primary rater. They reveal targeting hypotheses.

## What has actually been collected

The existing committed miner sweeps contain 1,206 unique public PR subjects with
full pin pairs. The existing label CSVs identify 48 distinct subjects; all 48
were checked against public PR metadata and the recorded commits on 2026-09-10.
No new verifier result was used to choose their target. Six remain unplaced:
three need a supported profile, two repeat an existing version-refresh family,
and one is a superseded proposal. They do not add coverage to the table below.

All placed targets use **`miner_label`**, which is **not verifier-independent**:
the historical worksheet exposed verifier verdicts. These are disclosed mining
hypotheses, never beta ground truth. The notes transcribe the existing rationale;
they do not assert that a human or a correct gate agrees with it. In particular,
closed/reverted status establishes an origin, not a security verdict, and the
MCP profile name does not assert reviewed declarations or binding completeness.
Human labels may move a candidate to another cell and reopen a gap; do not edit
a label to preserve the counts. Earlier pre-1.0 labels are not imported.

The figures below count **pinned** candidates only. Gaps have neither an origin
observation nor holdout capacity. Surplus candidates in one cell cannot fill a
different cell, so a large total cannot make the corpus ready.

| Measure | Count |
|---|---|
| Required cases | 80 |
| Required cells | 21 |
| Inventory slots including gaps | 84 |
| Pinned candidates | 42 |
| Unfilled candidate slots | 42 |
| Pinned qualifying-origin candidates | 42 |
| Required qualifying origins | 32 |
| Pinned potential holdout candidates | 32 |
| Pinned tuning-only candidates | 10 |
| Required holdout slots across cells | 23 |
| Holdout slots still without a potential candidate | 3 |
| Unplaced reserve candidates | 6 |

Per cell, `Potential holdout` means only that the **recorded** exposure has no
holdout-blocking mark. It is not a frozen split or accepted independent evidence.

| Profile | Target | Required | Pinned | Potential holdout | Holdout floor | Gaps |
|---|---|---|---|---|---|---|
| `mcp_openapi_declared_binding` | `passed` | 6 | 3 | 3 | 2 | 3 |
| `mcp_openapi_declared_binding` | `review_required` | 4 | 2 | 2 | 1 | 2 |
| `mcp_openapi_declared_binding` | `blocked` | 6 | 4 | 3 | 2 | 2 |
| `openai_agents_sdk` | `passed` | 5 | 4 | 3 | 1 | 1 |
| `openai_agents_sdk` | `review_required` | 3 | 3 | 3 | 1 | 0 |
| `openai_agents_sdk` | `blocked` | 4 | 1 | 1 | 1 | 3 |
| `langchain_crewai` | `passed` | 5 | 1 | 1 | 1 | 4 |
| `langchain_crewai` | `review_required` | 3 | 2 | 2 | 1 | 1 |
| `langchain_crewai` | `blocked` | 4 | 1 | 1 | 1 | 3 |
| `google_adk` | `passed` | 3 | 1 | 0 | 1 | 2 |
| `google_adk` | `review_required` | 2 | 4 | 2 | 1 | 0 |
| `google_adk` | `blocked` | 3 | 1 | 1 | 1 | 2 |
| `n8n` | `passed` | 3 | 1 | 1 | 1 | 2 |
| `n8n` | `review_required` | 2 | 2 | 2 | 1 | 0 |
| `n8n` | `blocked` | 3 | 0 | 0 | 1 | 3 |
| `multi_agent_handoffs` | `passed` | 4 | 1 | 1 | 1 | 3 |
| `multi_agent_handoffs` | `review_required` | 3 | 1 | 1 | 1 | 2 |
| `multi_agent_handoffs` | `blocked` | 5 | 1 | 1 | 1 | 4 |
| `coding_agent_trust_roots` | `passed` | 4 | 3 | 0 | 1 | 1 |
| `coding_agent_trust_roots` | `review_required` | 3 | 5 | 3 | 1 | 0 |
| `coding_agent_trust_roots` | `blocked` | 5 | 1 | 1 | 1 | 4 |

## Exposure and the human handoff

The [existing exposure rule](strata-inventory.md#exposure-and-why-it-decides-the-split) remains:
`engine_tests`, `maintainer_walk` and `shipped_sample` force `tuning_only`;
`benchmark_scored` and `miner_label` disclose exposure but do not alone prove
engine tuning. `either` is only potential eligibility under that rule.
Known marks from the pre-1.0 inventory are retained, and references in engine
source/tests are checked again. Missing searchable evidence does not prove an
unseen development history clean: the corpus owner must resolve private tuning,
related changes, prior rater exposure and independence before freezing a split.
Any evidence of tuning moves a candidate to `tuning_only`; no mark may be erased
to fill a holdout floor. Newly walking a diff for `diff_substance` would also
add `maintainer_walk`, exactly as Cut A requires.

Two public implementation records establish additional `maintainer_walk`
exposure that literal engine/test searches and the older inventory miss:
[PR #256](https://github.com/ThreeMoonsLab/agents-shipgate/pull/256), merged as
`bc4ddef4d239465816ec8942354fc579a2bc3dc6`, calibrated its fixes against all ten
W26 subjects; [PR #582](https://github.com/ThreeMoonsLab/agents-shipgate/pull/582),
merged as `e99e5dc76af6d194ebf2eca15800748287f2eef8`, developed the scoped-input
repair and validated the two ADK subjects. The beta guards retain these public
exposure floors even when a regression fixture uses a generic name.

| Source family | Known development exposure |
|---|---|
| `github.com/stripe/ai#400`, `#353`, `#338`, `#336`, `#332`, `#312` | #256 calibration; `maintainer_walk`, including the two unplaced version-refresh reserves. |
| `github.com/aaif-goose/goose#9798`, `#9684`, `#9637`, `#9717` | #256 calibration; `maintainer_walk`, including all three unplaced Goose reserves. |
| `github.com/google/adk-samples#1975`, `#1977` | #582 development/validation; `maintainer_walk`. |

The pre-existing source record also identifies
`github.com/google/adk-samples#125` and `github.com/google/adk-samples#2148` as
the original and reimplemented auto-insurance graph. Their provisional profiles
differ, but they are a related family: resolve admissibility together and keep
them in one split. Do not use one for tuning and the other as holdout or treat
distinct PR numbers as independent diversity. Their potential counts remain
conditional on that owner review, not accepted case or holdout coverage.

Profiles are provisional sourcing assignments from the existing rationale and
changed paths, preserving the old assignment where one exists. Scenario profiles
require a delegation edge or coding-agent trust surface, not a repository name.
The register gives that context without interpreting a new diff or supplying a
missing effect, authority or deployed binding declaration.

Before labeling, #512 still needs actual human primary raters from both required
disciplines, their independence, independent adjudication and a complete blind
packet for each admitted case. The residual rater output-contract correction is
tracked in [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520): the
current guide's JSON example still allows expected IE despite the approved
three-outcome policy. Source collection can proceed; do not hand that unchanged
contract to beta raters. Neither a sourcing PR nor agent review satisfies human
participation. Freeze labels before any beta receipt/scoring run, then bind the
final candidate wheel, source and policy under #569/#509. No source here creates
design-partner consent or satisfies #571's longitudinal workflow observations.

## Pins and public state

For merged PRs, the recorded head equals the GitHub merge commit and its first
parent equals the recorded base. For closed-unmerged PRs, the recorded fork-point
base and final PR head remain as mined; the live comparison corroborates ancestry,
not a replacement for historical fork-point selection. A live base-branch tip is
never a pin. Every placed pair matches every committed sweep recording of that
subject; contradictory pairs must be resolved before placement.

The commit links below let a reviewer inspect the immutable head and parents;
the CSV supplies both full SHAs. Public status was observed on 2026-09-10, not
asserted forever. Re-check it when admitting a packet. All fetched changed-path
lists were below GitHub's 300-file comparison limit; paths are profile context,
not proof of complete capability coverage. No source or downstream agent was run.

The reverted coding-agent workflow change's original pins are retained.
[Its revert](https://github.com/pydantic/pydantic-ai/pull/4202), merge
`f74a093ae387b3bc92970c65eb6eef81e4be2b29`, cites case-insensitive filename
collisions. That proves a revert occurred; it is **not** reviewer agreement with
the miner's security rationale.

## Candidate register

`State` uses the existing register vocabulary: `closed` means closed without
merge; `reverted` preserves the original merge pins. `Source context` names
changed paths and the reason for the profile, not an accepted verdict.

| Candidate | Profile | State | Pinned head | Source context |
|---|---|---|---|---|
| `github.com/pydantic/pydantic-ai#4199` | `coding_agent_trust_roots` | `reverted` | [commit](https://github.com/pydantic/pydantic-ai/commit/282b30ea07cdd60103d53598ff50ff48340fe9a8) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `.github/workflows/at-claude.yml`, `.github/workflows/review.yml`, `AGENTS.md`, `docs/AGENTS.md`, `docs/CLAUDE.md`, `pydantic_ai_slim/pydantic_ai/models/AGENTS.md`, `pydantic_ai_slim/pydantic_ai/models/CLAUDE.md` |
| `github.com/aaif-goose/goose#9637` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/aaif-goose/goose/commit/59521f455ff39b0455b2771ba497d6a0549efbd2) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `evals/harbor/.agents/skills/compare_tasks/SKILL.md`, `evals/harbor/recipes/analyze_bench_failure.yaml`, `evals/harbor/recipes/compare_bench_run.yaml` |
| `github.com/stripe/ai#353` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/stripe/ai/commit/c0a156cdb5bc4c0864200f9fa24ec0237702002c) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `providers/claude/plugin/skills/stripe-best-practices/SKILL.md`, `providers/claude/plugin/skills/stripe-best-practices/references/security.md` (examples; see the pinned comparison) |
| `github.com/stripe/ai#400` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/stripe/ai/commit/f6e8ff385e15f24cee5986518221228b7dc9ac6d) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `providers/claude/plugin/skills/stripe-best-practices/SKILL.md`, `providers/claude/plugin/skills/upgrade-stripe/SKILL.md`, `providers/cursor/plugin/skills/stripe-best-practices/SKILL.md`, `providers/cursor/plugin/skills/upgrade-stripe/SKILL.md`, `skills/stripe-best-practices/SKILL.md`, `skills/upgrade-stripe/SKILL.md` |
| `github.com/aaif-goose/goose#10825` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/aaif-goose/goose/commit/022c17c3946b506b031fc828543cb9e6650dbfd5) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `.github/workflows/recipe-security-scanner.yml` |
| `github.com/aaif-goose/goose#9736` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/aaif-goose/goose/commit/b0d8ea055cf4e21e9903385e9e12bfe21a045dcf) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `crates/goose/src/config/paths.rs`, `crates/goose/src/hints/load_hints.rs` |
| `github.com/modelcontextprotocol/servers#4739` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/modelcontextprotocol/servers/commit/2e3e4c7ab74e08c664974e147620785d5748a3c9) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `.github/workflows/readme-pr-check.yml` |
| `github.com/stripe/ai#312` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/stripe/ai/commit/91f8471adfe5189145aaab61879bfa13452c51eb) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `.github/workflows/sync-skills.yml`, `providers/claude/plugin/skills/stripe-best-practices/SKILL.md` (examples; see the pinned comparison) |
| `github.com/stripe/ai#338` | `coding_agent_trust_roots` | `merged` | [commit](https://github.com/stripe/ai/commit/42954a7f0946d687285c820f5b85a4bcb8357e25) | Coding-agent skill, instruction, host configuration or GitHub workflow surface; the scenario takes precedence over the repository framework. `providers/claude/plugin/skills/stripe-projects/SKILL.md`, `providers/cursor/plugin/skills/stripe-projects/SKILL.md`, `skills/stripe-projects/SKILL.md` |
| `github.com/google/adk-samples#2148` | `google_adk` | `closed` | [commit](https://github.com/google/adk-samples/commit/ed0f7ba1bc449e792b08763b7a6d8d5fb3da6793) | ADK example, agent configuration or SDK integration change. `core/auto-insurance-agent/.env.example`, `core/auto-insurance-agent/README.md` (examples; see the pinned comparison) |
| `github.com/google/adk-samples#1977` | `google_adk` | `merged` | [commit](https://github.com/google/adk-samples/commit/964b975ee158a01e9f61fa52d432b49ed4a396d4) | ADK example, agent configuration or SDK integration change. `python/agents/travel-planner-google-maps-mcp/README.md`, `python/agents/travel-planner-google-maps-mcp/travel_planner_agent/__init__.py`, `python/agents/travel-planner-google-maps-mcp/travel_planner_agent/agent.py`, `python/agents/travel-planner-google-maps-mcp/travel_planner_agent/skills/travel-concierge/SKILL.md` |
| `github.com/google/adk-python#6605` | `google_adk` | `closed` | [commit](https://github.com/google/adk-python/commit/977153df5df466d8d59b0a97bd772e68897c67e0) | ADK example, agent configuration or SDK integration change. `contributing/samples/agent_hooks/README.md`, `contributing/samples/agent_hooks/__init__.py` (examples; see the pinned comparison) |
| `github.com/google/adk-samples#1731` | `google_adk` | `closed` | [commit](https://github.com/google/adk-samples/commit/72c4ffe018e0e3b61d3345e20c2223d47afcf58e) | ADK example, agent configuration or SDK integration change. `python/agents/README.md`, `python/agents/openregistry-cross-border-kyc/.env.example` (examples; see the pinned comparison) |
| `github.com/google/adk-samples#1975` | `google_adk` | `merged` | [commit](https://github.com/google/adk-samples/commit/4f8c74236398c5255180de8ae6bef935d4840409) | ADK example, agent configuration or SDK integration change. `python/agents/travel-panner-google-maps-mcp/README.md`, `python/agents/travel-panner-google-maps-mcp/travel_planner_agent/__init__.py`, `python/agents/travel-panner-google-maps-mcp/travel_planner_agent/agent.py`, `python/agents/travel-panner-google-maps-mcp/travel_planner_agent/skills/travel-concierge/SKILL.md` |
| `github.com/google/adk-samples#348` | `google_adk` | `merged` | [commit](https://github.com/google/adk-samples/commit/5401edac5a51fd3367377d62d19d1f0fa23c407b) | ADK example, agent configuration or SDK integration change. `README.md`, `python/agents/README.md`, `python/agents/antom-payment/.env.example`, `python/agents/antom-payment/README.md`, `python/agents/antom-payment/antom-payemnt-agent/__init__.py`, `python/agents/antom-payment/antom-payemnt-agent/agent.py`, `python/agents/antom-payment/pyproject.toml` |
| `github.com/crewAIInc/crewAI-examples#169` | `langchain_crewai` | `merged` | [commit](https://github.com/crewAIInc/crewAI-examples/commit/660c7dbdabb36fcd1ec95ee0cca4412e00f0d4c6) | CrewAI examples or LangChain agent/integration source change. `email_auto_responder_flow/.gitignore`, `email_auto_responder_flow/Automating_Tasks_with_CrewAI.md` (examples; see the pinned comparison) |
| `github.com/crewAIInc/crewAI-examples#184` | `langchain_crewai` | `merged` | [commit](https://github.com/crewAIInc/crewAI-examples/commit/14b09755f9806f014d623f98ccecd8bfcfd76b12) | CrewAI examples or LangChain agent/integration source change. `markdown_validator/.env.example`, `markdown_validator/MarkdownTools.py` (examples; see the pinned comparison) |
| `github.com/bytedance/deer-flow#4868` | `langchain_crewai` | `merged` | [commit](https://github.com/bytedance/deer-flow/commit/7e95bef2e73162306fd5eeb54ab928364497ecd8) | CrewAI examples or LangChain agent/integration source change. `backend/app/gateway/routers/mcp.py`, `backend/app/gateway/services.py` (examples; see the pinned comparison) |
| `github.com/langchain-ai/deepagents#5999` | `langchain_crewai` | `merged` | [commit](https://github.com/langchain-ai/deepagents/commit/568b398df9b9f4f3464b4107c0ef9001f530d728) | CrewAI examples or LangChain agent/integration source change. `.github/workflows/_test.yml`, `.github/workflows/release.yml` (examples; see the pinned comparison) |
| `github.com/elastic/mcp-server-elasticsearch#57` | `mcp_openapi_declared_binding` | `closed` | [commit](https://github.com/elastic/mcp-server-elasticsearch/commit/d8ceb24770b9bc80dfbacb8948364f6ace1fb2f8) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `README.md`, `index.ts`, `package-lock.json`, `package.json`, `yarn.lock` |
| `github.com/elastic/mcp-server-elasticsearch#69` | `mcp_openapi_declared_binding` | `closed` | [commit](https://github.com/elastic/mcp-server-elasticsearch/commit/39033c1dcdf26157279f17e226645f159c5adbe9) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `README.md`, `create_mock_data.sh`, `index.ts` |
| `github.com/hashicorp/terraform-mcp-server#469` | `mcp_openapi_declared_binding` | `merged` | [commit](https://github.com/hashicorp/terraform-mcp-server/commit/5b8df71b72c9c7ae388b4543f2624f56815491eb) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `CHANGELOG.md`, `pkg/tools/dynamic_tool.go`, `pkg/tools/tfe/action_run.go`, `pkg/tools/tfe/delete_team.go`, `pkg/tools/tfe/delete_team_test.go`, `pkg/tools/tfe/force_unlock_workspace.go`, `pkg/toolsets/mapping.go` |
| `github.com/stripe/ai#232` | `mcp_openapi_declared_binding` | `merged` | [commit](https://github.com/stripe/ai/commit/cd8cee575064db6ae00cee9984f976dd5055f9c2) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `tools/python/MIGRATION.md`, `tools/python/examples/crewai/main.py` (examples; see the pinned comparison) |
| `github.com/cloudflare/mcp-server-cloudflare#414` | `mcp_openapi_declared_binding` | `merged` | [commit](https://github.com/cloudflare/mcp-server-cloudflare/commit/502ce24912015d9d844b46eaa9543eff6879e8bb) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `.changeset/cloudflare-blog-mcp-server.md`, `README.md` (examples; see the pinned comparison) |
| `github.com/cloudflare/mcp-server-cloudflare#433` | `mcp_openapi_declared_binding` | `merged` | [commit](https://github.com/cloudflare/mcp-server-cloudflare/commit/cf04d31fd7bafcadbcefa94300dc4336435dbeea) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `.changeset/developer-stack-mcp.md`, `apps/stack-mcp/README.md`, `apps/stack-mcp/src/stack-mcp.spec.ts`, `apps/stack-mcp/src/tools/stack.tools.ts` |
| `github.com/hashicorp/terraform-mcp-server#451` | `mcp_openapi_declared_binding` | `merged` | [commit](https://github.com/hashicorp/terraform-mcp-server/commit/6f302c3cfbf7fe510bccb3889284768fe89aa7bf) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `CHANGELOG.md`, `pkg/tools/dynamic_tool.go`, `pkg/tools/tfe/get_team.go`, `pkg/tools/tfe/get_team_test.go`, `pkg/toolsets/mapping.go` |
| `github.com/hashicorp/terraform-mcp-server#461` | `mcp_openapi_declared_binding` | `merged` | [commit](https://github.com/hashicorp/terraform-mcp-server/commit/45257192edfb390c1dc009ce2c566f1a920b9462) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `CHANGELOG.md`, `pkg/tools/dynamic_tool.go`, `pkg/tools/tfe/grant_team_access.go`, `pkg/tools/tfe/grant_team_access_test.go`, `pkg/toolsets/mapping.go` |
| `github.com/hashicorp/terraform-mcp-server#493` | `mcp_openapi_declared_binding` | `merged` | [commit](https://github.com/hashicorp/terraform-mcp-server/commit/0b9d2d31cbc6c8c2dc429c4cacee601a0b5f22b1) | MCP registration or published tool-surface change; the profile is a sourcing hypothesis, not a claim that reviewed bindings exist. `Makefile`, `pkg/mcp-official/client/tfe_client.go`, `pkg/mcp-official/server.go`, `pkg/mcp-official/tools.go`, `pkg/mcp-official/tools/tfe/organizations.go`, `pkg/mcp-official/tools/tfe/workspace.go`, `pkg/mcp-official/tools/tools.go` |
| `github.com/google/adk-samples#125` | `multi_agent_handoffs` | `merged` | [commit](https://github.com/google/adk-samples/commit/ea2288ccc27e259ec837e4c321640e22bf5518e7) | The existing source rationale describes delegation between agents, not merely a repository containing several agents. `.gitignore`, `python/agents/README.md` (examples; see the pinned comparison) |
| `github.com/pydantic/pydantic-ai#3248` | `multi_agent_handoffs` | `merged` | [commit](https://github.com/pydantic/pydantic-ai/commit/8dc9b80cc4ca9ac0cbe7792e6e4aef4f1b1e8cba) | The existing source rationale describes delegation between agents, not merely a repository containing several agents. `docs/examples/medical-agent-delegation.md`, `examples/pydantic_ai_examples/medical_agent_delegation.py` |
| `github.com/pydantic/pydantic-ai#5120` | `multi_agent_handoffs` | `merged` | [commit](https://github.com/pydantic/pydantic-ai/commit/ee1048b6c9ff65674cc72699ef758b8edd778834) | The existing source rationale describes delegation between agents, not merely a repository containing several agents. `docs/capabilities.md`, `docs/models/xai.md` (examples; see the pinned comparison) |
| `github.com/enescingoz/awesome-n8n-templates#134` | `n8n` | `merged` | [commit](https://github.com/enescingoz/awesome-n8n-templates/commit/f0935583632a1e69bdd0df0b7f46ff72445c7313) | Exported n8n workflow change. `OpenAI_and_LLMs/Ollama_Basic_Workflow.json`, `README.md` |
| `github.com/Zie619/n8n-workflows#87` | `n8n` | `merged` | [commit](https://github.com/Zie619/n8n-workflows/commit/07ddbb96cea303b49a3ef910c1b520ee551f8d36) | Exported n8n workflow change. `workflows/Telegram/Academic Assistant Chatbot (Telegram + OpenAI).json` |
| `github.com/enescingoz/awesome-n8n-templates#161` | `n8n` | `merged` | [commit](https://github.com/enescingoz/awesome-n8n-templates/commit/60e33c6113a1fff269d40e23a69498cb2a396212) | Exported n8n workflow change. `README.md`, `Telegram/Bitcoin price alert to Telegram with CoinPaprika.json` |
| `github.com/openai/openai-agents-python#2932` | `openai_agents_sdk` | `closed` | [commit](https://github.com/openai/openai-agents-python/commit/5343423279075ec721e2ce6aaab11bef7e1aeab9) | OpenAI Agents SDK source/tool/sandbox change. `examples/mcp/hashlock_example/README.md`, `examples/mcp/hashlock_example/main.py` |
| `github.com/openai/openai-agents-python#3392` | `openai_agents_sdk` | `merged` | [commit](https://github.com/openai/openai-agents-python/commit/8dc30e4807085dd0d2a382fa61779ea1b6bfdea4) | OpenAI Agents SDK source/tool/sandbox change. `docs/ja/agents.md`, `docs/ja/config.md` (examples; see the pinned comparison) |
| `github.com/openai/openai-agents-python#3451` | `openai_agents_sdk` | `merged` | [commit](https://github.com/openai/openai-agents-python/commit/f6ba91b120b97e38a4cdb3e61b226cef348679eb) | OpenAI Agents SDK source/tool/sandbox change. `src/agents/extensions/models/any_llm_model.py`, `src/agents/extensions/models/litellm_model.py`, `src/agents/mcp/server.py`, `src/agents/model_settings.py`, `src/agents/models/_trace.py`, `src/agents/models/openai_chatcompletions.py`, `src/agents/realtime/session.py`, `src/agents/run_context.py`, `src/agents/run_internal/agent_runner_helpers.py` |
| `github.com/openai/openai-agents-python#3461` | `openai_agents_sdk` | `merged` | [commit](https://github.com/openai/openai-agents-python/commit/45effb4b7d7de1226ebba7ba304bccfcf0a37fdf) | OpenAI Agents SDK source/tool/sandbox change. `src/agents/__init__.py`, `src/agents/run.py`, `src/agents/run_config.py`, `src/agents/run_internal/run_steps.py`, `src/agents/run_internal/turn_resolution.py` |
| `github.com/openai/openai-agents-python#3518` | `openai_agents_sdk` | `merged` | [commit](https://github.com/openai/openai-agents-python/commit/921135630b83c5e1387b064ad5fec89a4c3230d4) | OpenAI Agents SDK source/tool/sandbox change. `examples/basic/agent_lifecycle_example.py`, `examples/basic/lifecycle_example.py`, `examples/sandbox/healthcare_support/workflow.py`, `src/agents/lifecycle.py` |
| `github.com/openai/openai-agents-python#3788` | `openai_agents_sdk` | `merged` | [commit](https://github.com/openai/openai-agents-python/commit/0354f482a8e76d33c50a6a3e462c814eefde1e6b) | OpenAI Agents SDK source/tool/sandbox change. `examples/agent_patterns/README.md`, `examples/agent_patterns/hosted_multi_agent_beta.py` (examples; see the pinned comparison) |
| `github.com/openai/openai-agents-python#3833` | `openai_agents_sdk` | `merged` | [commit](https://github.com/openai/openai-agents-python/commit/965335aba6f6c71500e0b8cdb4e9e495f5801d4d) | OpenAI Agents SDK source/tool/sandbox change. `examples/tools/programmatic_tool_calling.py`, `src/agents/__init__.py` (examples; see the pinned comparison) |
| `github.com/openai/openai-agents-python#4399` | `openai_agents_sdk` | `closed` | [commit](https://github.com/openai/openai-agents-python/commit/9e8ee09632e3f968e2286b042cdd7eb5d1226598) | OpenAI Agents SDK source/tool/sandbox change. `src/agents/sandbox/manifest.py` |

### Reserve

Unplaced subjects do not contribute a slot, origin floor or holdout count.
Their known source pins remain in the existing sweeps; promoting a reserve
requires resolving the stated issue and rechecking exposure and related-case
leakage. A replacement replaces its related candidate, not a second observation.

| Candidate | Origin | State | Reason to keep unplaced |
|---|---|---|---|
| `github.com/aaif-goose/goose#9684` | `real_history` | `merged` | Release and model/provider catalog churn; the collected paths do not establish a coding-agent trust-root scenario. Resolve profile relevance before placement. Known maintainer_walk from implementation calibration in PR #256; tuning_only if promoted. |
| `github.com/aaif-goose/goose#9798` | `real_history` | `merged` | ACP session metadata; no supported profile assigned from the existing rationale. Establish profile relevance before placing it. Known maintainer_walk from implementation calibration in PR #256; tuning_only if promoted. |
| `github.com/aaif-goose/goose#9717` | `real_history` | `merged` | Goose server/ACP route removal; no supported profile assigned from the existing rationale. Establish profile relevance before placing it. Known maintainer_walk from implementation calibration in PR #256; tuning_only if promoted. |
| `github.com/stripe/ai#332` | `real_history` | `merged` | Same existing-skill API-version refresh family as the placed newer change; replacement only, not additional diversity or a cross-split counterpart. Known maintainer_walk from implementation calibration in PR #256; tuning_only if promoted. |
| `github.com/stripe/ai#336` | `real_history` | `merged` | Same existing-skill API-version refresh family as the placed newer change; replacement only, not additional diversity or a cross-split counterpart. Known maintainer_walk from implementation calibration in PR #256; tuning_only if promoted. |
| `github.com/hashicorp/terraform-mcp-server#422` | `rejected_or_reverted` | `closed` | Closed initial delete_team submission superseded by the placed merged proposal; replacement only, never independent holdout for that proposal. |

## Next sourcing work

Fill cells with no potential holdout candidate first, then the remaining
blocked and safe gaps; preserve per-cell balance instead of accumulating more
review examples. Each gap's `mining_lead` names repositories and a change shape.
The lead is a search intention, not an assertion that such a case exists there.
Prefer distinct real changes; a synthetic construction needs its own design
record and cannot count as a real-history origin. Keep any target/design record
outside the eventual blind packet.

When adding a candidate, update the CSV and this register together, recheck the
full pin pair and source state, preserve exposure, and run
`pytest tests/test_beta_strata_inventory.py tests/test_strata_inventory.py`.
Those guards check the sourcing plan's truthfulness. They do not demand that
an incomplete plan pretend to be ready, and they cannot qualify a release.
